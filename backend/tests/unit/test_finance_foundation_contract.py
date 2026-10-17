from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from apps.common.api.schema import phase_1a_endpoints
from apps.common.finance.exceptions import IdempotencyRequestInvalid
from apps.common.finance.idempotency import canonical_request_hash
from apps.common.finance.money import InvalidMoney, parse_money
from tests.unit.test_openapi_contract import PUBLISHED_PATHS

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.v1.json"


def test_money_parses_and_quantizes_half_up() -> None:
    assert parse_money("10.005") == Decimal("10.01")
    assert parse_money("10.004") == Decimal("10.00")
    assert parse_money("-1.005") == Decimal("-1.01")
    assert parse_money("1.5") == Decimal("1.50")
    assert parse_money(2) == Decimal("2.00")
    assert parse_money(Decimal("2")) == Decimal("2.00")
    assert parse_money("9999999999999999.99") == Decimal("9999999999999999.99")
    assert parse_money("0.00") == Decimal("0.00")


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        True,
        False,
        float("nan"),
        float("inf"),
        "NaN",
        "Infinity",
        "1e2",
        "1E-2",
        " 1.00",
        "+1.00",
        "",
        "00.10",
        "10000000000000000.00",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1E+2"),
        Decimal("1E-9"),
        object(),
    ],
)
def test_money_rejects_inexact_or_oversized_input(value: object) -> None:
    with pytest.raises(InvalidMoney):
        parse_money(value)


def test_request_hash_is_canonical_and_bound_to_scope() -> None:
    gym_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    route = "/api/v1/payments/"
    left = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=route,
        body={"b": 1, "a": {"z": 1, "y": [1, 2]}},
    )
    right = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=route,
        body={"a": {"y": [1, 2], "z": 1}, "b": 1},
    )
    assert left == right
    assert len(left) == 64
    assert left == left.lower()
    reordered = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=route,
        body={"a": {"y": [2, 1], "z": 1}, "b": 1},
    )
    assert reordered != left
    assert canonical_request_hash(
        gym_id=uuid.uuid4(),
        actor_membership_id=actor_id,
        http_method="POST",
        route=route,
        body={"a": 1},
    ) != canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=None,
        http_method="POST",
        route=route,
        body={"a": 1},
    )
    with pytest.raises(IdempotencyRequestInvalid):
        canonical_request_hash(
            gym_id=gym_id,
            actor_membership_id=actor_id,
            http_method="POST",
            route=route,
            body={"amount": 1.25},
        )


def test_finance_idempotency_settings_are_bounded_and_separate() -> None:
    assert settings.FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES == 64 * 1024
    assert settings.FINANCE_IDEMPOTENCY_TTL_SECONDS == 24 * 60 * 60
    assert settings.FINANCE_IDEMPOTENCY_CLAIM_SECONDS == 30
    assert settings.FINANCE_IDEMPOTENCY_ENCRYPTION_KEY
    assert settings.FINANCE_IDEMPOTENCY_ENCRYPTION_KEY != settings.MFA_ENCRYPTION_KEY


def test_only_marked_domain_failures_are_cacheable() -> None:
    from types import SimpleNamespace

    from django.db import OperationalError
    from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
    from rest_framework.renderers import JSONRenderer

    from apps.common.api.exceptions import api_exception_handler
    from apps.common.finance.exceptions import FinanceStateConflict, IdempotencyResponseTooLarge
    from apps.common.finance.idempotency import (
        IdempotentResponse,
        _client_failure_body,
        _validate_response,
    )

    request = SimpleNamespace(request_id="req_finance_domain_01")
    failure = IdempotencyResponseTooLarge()
    context = {"request": request}
    expected = JSONRenderer().render(api_exception_handler(failure, context).data)
    assert _client_failure_body(failure, request) == (422, expected)
    assert b"req_finance_domain_01" in expected
    conflict = FinanceStateConflict()
    conflict_body = JSONRenderer().render(api_exception_handler(conflict, context).data)
    assert _client_failure_body(conflict, request) == (409, conflict_body)
    assert _client_failure_body(ValidationError({"amount": ["Invalid."]}), request) is None
    assert _client_failure_body(PermissionDenied(), request) is None
    assert _client_failure_body(Throttled(wait=1), request) is None
    assert _client_failure_body(OperationalError("deadlock detected"), request) is None
    assert _client_failure_body(RuntimeError("boom-secret-body"), request) is None
    with pytest.raises(RuntimeError, match="2xx"):
        _validate_response(IdempotentResponse(status=500, body=b'{"error":true}'))


def test_fernet_ciphertext_bound_matches_max_plaintext() -> None:
    from apps.common.finance.idempotency import _fernet
    from apps.tenants.models import IDEMPOTENCY_CIPHERTEXT_MAX_OCTETS

    token = _fernet().encrypt(b"a" * settings.FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES)
    assert len(token) == IDEMPOTENCY_CIPHERTEXT_MAX_OCTETS == 87480


def test_finance_key_cannot_reuse_the_mfa_key() -> None:
    from apps.common.finance.idempotency import _fernet

    with (
        override_settings(
            FINANCE_IDEMPOTENCY_ENCRYPTION_KEY="same-secret-value",
            MFA_ENCRYPTION_KEY="same-secret-value",
        ),
        pytest.raises(RuntimeError, match="must not match"),
    ):
        _fernet()


def test_published_path_set_is_unchanged() -> None:
    endpoints = [(path, "path", "GET", None) for path in sorted(PUBLISHED_PATHS)]
    extras = [
        ("/api/v1/payments/", "path", "GET", None),
        ("/api/v1/receipts/", "path", "GET", None),
        ("/api/v1/expenses/", "path", "GET", None),
        ("/api/v1/expense-categories/", "path", "GET", None),
        ("/api/v1/retail-sales/", "path", "GET", None),
        ("/api/v1/products/", "path", "GET", None),
    ]
    published = {path for path, *_rest in phase_1a_endpoints([*endpoints, *extras])}
    document = json.loads(CONTRACT_PATH.read_text())
    assert published == PUBLISHED_PATHS
    assert set(document["paths"]) == PUBLISHED_PATHS

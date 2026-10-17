"""Finance idempotency claims.

Lock order:

1. The claim transaction locks only the ``IdempotencyRecord`` row, then commits
   before any business row is locked. A concurrent duplicate either waits on
   the unique key and then sees the live claim, or reads the committed claim.
2. The business transaction runs the caller's mutation first. Sequence
   allocation, when used, locks that ``TenantSequence`` row inside this
   transaction. The idempotency row is locked again only after the mutation
   returns, and the completion is written in the same transaction.
3. An exception rolls that business transaction back, so a committed finance
   effect cannot outlive an unfinished claim. Only a
   ``CacheableIdempotentFailure`` is stored as ``FAILED``, and only while this
   attempt still owns ``locked_until``. Validation, permission, authentication,
   throttle, database, and other unexpected failures release that claim.

``mutate`` must be database-only. It must not send mail, charge a gateway, or
do any other irreversible external I/O. Completion is fenced by the claim's
``locked_until``. A claimant that resumes after another transaction reclaimed
the lease rolls its business work back and raises ``IdempotencyInProgress``.

``ATOMIC_REQUESTS`` is on. An idempotent command view must use
``non_atomic_requests`` so this runner can commit the claim before the
business transaction. This runner owns that business transaction. Approval
and posting helpers belong inside ``mutate`` and must not start another
durable idempotency transaction.

Keys, request bodies, and ciphertext are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.renderers import JSONRenderer

from apps.common.api.exceptions import api_exception_handler
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.exceptions import (
    IdempotencyInProgress,
    IdempotencyKeyExpired,
    IdempotencyRequestInvalid,
    IdempotencyResponseTooLarge,
)
from apps.tenants.exceptions import IdempotencyKeyReused
from apps.tenants.models import IdempotencyRecord

_METHOD_RE = re.compile(r"^[A-Z]{3,8}$")
_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_./{}-]{1,199}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_UNIQUE_VIOLATION = "23505"


@dataclass(frozen=True)
class IdempotentResponse:
    status: int
    body: bytes
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None


@dataclass(frozen=True)
class IdempotentResult:
    replayed: bool
    status: int
    body: bytes
    resource_type: str | None
    resource_id: uuid.UUID | None


@dataclass(frozen=True)
class _Claim:
    record_id: uuid.UUID
    locked_until: datetime
    replay: IdempotentResult | None


def canonical_request_hash(
    *,
    gym_id: uuid.UUID,
    actor_membership_id: uuid.UUID | None,
    http_method: str,
    route: str,
    body: Mapping[str, object],
) -> str:
    """Hash the gym, actor, method, canonical route, and validated body."""

    _validate_scope(http_method=http_method, route=route, idempotency_key=None)
    document = {
        "actor_membership_id": (None if actor_membership_id is None else str(actor_membership_id)),
        "body": _canonical_body(body),
        "gym_id": str(gym_id),
        "http_method": http_method,
        "route": route,
    }
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def run_idempotent(
    *,
    gym_id: uuid.UUID,
    actor_membership_id: uuid.UUID | None,
    http_method: str,
    route: str,
    idempotency_key: str,
    body: Mapping[str, object],
    mutate: Callable[[], IdempotentResponse],
    created_by_membership_id: uuid.UUID | None = None,
    request: object | None = None,
) -> IdempotentResult:
    """Claim the key, run ``mutate``, and commit the outcome with its effects.

    ``ATOMIC_REQUESTS`` wraps ordinary views in a transaction. Idempotent
    command views must opt out with ``non_atomic_requests``. This function
    then owns the claim transaction and the business transaction. ``mutate``
    is database-only: approval and posting helpers run there and must not
    call this function again or open another durable idempotency transaction.

    Authenticate, authorize, throttle, and validate the serializer before
    calling this function. Those failures must not be cached. ``mutate``
    returns only a 2xx ``IdempotentResponse``. A deterministic finance-domain
    failure is a ``CacheableIdempotentFailure`` raised from ``mutate``;
    ``request`` is the context used to render that response, including its
    request id.
    """

    if connection.in_atomic_block:
        raise RuntimeError("An idempotent finance command must start its own transaction.")
    _validate_scope(http_method=http_method, route=route, idempotency_key=idempotency_key)
    request_hash = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_membership_id,
        http_method=http_method,
        route=route,
        body=body,
    )
    creator = actor_membership_id if created_by_membership_id is None else created_by_membership_id
    claim = _claim(
        gym_id=gym_id,
        actor_membership_id=actor_membership_id,
        created_by_membership_id=creator,
        http_method=http_method,
        route=route,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if claim.replay is not None:
        return claim.replay
    try:
        with transaction.atomic(durable=True):
            set_gym_context(gym_id=gym_id)
            response = mutate()
            _validate_response(response)
            _complete(
                gym_id=gym_id,
                record_id=claim.record_id,
                expected_locked_until=claim.locked_until,
                request_hash=request_hash,
                response=response,
            )
    except Exception as exc:
        cached = _client_failure_body(exc, request)
        if cached is None:
            _release_claim(
                gym_id=gym_id,
                record_id=claim.record_id,
                expected_locked_until=claim.locked_until,
                request_hash=request_hash,
            )
        else:
            status_code, failure_body = cached
            _record_failure(
                gym_id=gym_id,
                record_id=claim.record_id,
                expected_locked_until=claim.locked_until,
                request_hash=request_hash,
                status_code=status_code,
                body=failure_body,
            )
        raise
    return IdempotentResult(
        replayed=False,
        status=response.status,
        body=response.body,
        resource_type=response.resource_type,
        resource_id=response.resource_id,
    )


def _validate_scope(*, http_method: str, route: str, idempotency_key: str | None) -> None:
    if not isinstance(http_method, str) or _METHOD_RE.fullmatch(http_method) is None:
        raise IdempotencyRequestInvalid()
    if not isinstance(route, str) or _ROUTE_RE.fullmatch(route) is None:
        raise IdempotencyRequestInvalid()
    if idempotency_key is None:
        return
    if not isinstance(idempotency_key, str) or _KEY_RE.fullmatch(idempotency_key) is None:
        raise IdempotencyRequestInvalid()


def _canonical_body(body: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(body, dict):
        raise IdempotencyRequestInvalid()
    canonical = _canonical_value(body)
    if not isinstance(canonical, dict):
        raise IdempotencyRequestInvalid()
    return canonical


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) or isinstance(value, bytes):
        raise IdempotencyRequestInvalid()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise IdempotencyRequestInvalid()
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise IdempotencyRequestInvalid()
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    raise IdempotencyRequestInvalid()


def _claim(
    *,
    gym_id: uuid.UUID,
    actor_membership_id: uuid.UUID | None,
    created_by_membership_id: uuid.UUID | None,
    http_method: str,
    route: str,
    idempotency_key: str,
    request_hash: str,
) -> _Claim:
    now = timezone.now()
    locked_until = now + timedelta(seconds=settings.FINANCE_IDEMPOTENCY_CLAIM_SECONDS)
    expires_at = now + timedelta(seconds=settings.FINANCE_IDEMPOTENCY_TTL_SECONDS)
    with transaction.atomic(durable=True):
        set_gym_context(gym_id=gym_id)
        record: IdempotencyRecord | None = None
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    gym_id=gym_id,
                    actor_membership_id=actor_membership_id,
                    http_method=http_method,
                    route=route,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    state=IdempotencyRecord.State.IN_PROGRESS,
                    locked_until=locked_until,
                    expires_at=expires_at,
                    created_by_membership_id=created_by_membership_id,
                )
        except IntegrityError as exc:
            if _sqlstate(exc) != _UNIQUE_VIOLATION:
                raise
            record = None
        if record is not None:
            record.refresh_from_db(fields=["locked_until"])
            if record.locked_until is None:
                raise IdempotencyInProgress()
            return _Claim(record.id, record.locked_until, None)
        existing = (
            IdempotencyRecord.objects.select_for_update()
            .filter(
                gym_id=gym_id,
                actor_membership_id=actor_membership_id,
                http_method=http_method,
                route=route,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if existing is None:
            raise IdempotencyInProgress()
        if existing.expires_at <= timezone.now():
            raise IdempotencyKeyExpired()
        if not hmac.compare_digest(existing.request_hash, request_hash):
            raise IdempotencyKeyReused()
        if existing.state in {
            IdempotencyRecord.State.COMPLETED,
            IdempotencyRecord.State.FAILED,
        }:
            return _Claim(existing.id, existing.updated_at, _replay(existing))
        if existing.locked_until is not None and existing.locked_until > timezone.now():
            raise IdempotencyInProgress()
        existing.locked_until = locked_until
        existing.save(update_fields=["locked_until", "updated_at"])
        existing.refresh_from_db(fields=["locked_until"])
        if existing.locked_until is None:
            raise IdempotencyInProgress()
        return _Claim(existing.id, existing.locked_until, None)


def _complete(
    *,
    gym_id: uuid.UUID,
    record_id: uuid.UUID,
    expected_locked_until: datetime,
    request_hash: str,
    response: IdempotentResponse,
) -> None:
    record = IdempotencyRecord.objects.select_for_update().get(pk=record_id, gym_id=gym_id)
    if (
        record.state != IdempotencyRecord.State.IN_PROGRESS
        or record.locked_until != expected_locked_until
        or not hmac.compare_digest(record.request_hash, request_hash)
    ):
        raise IdempotencyInProgress()
    record.state = IdempotencyRecord.State.COMPLETED
    record.response_status = response.status
    record.response_body_ciphertext = _encrypt(response.body)
    record.response_body_bytes = len(response.body)
    record.resource_type = response.resource_type
    record.resource_id = response.resource_id
    record.locked_until = None
    record.save(
        update_fields=[
            "state",
            "response_status",
            "response_body_ciphertext",
            "response_body_bytes",
            "resource_type",
            "resource_id",
            "locked_until",
            "updated_at",
        ]
    )


def _record_failure(
    *,
    gym_id: uuid.UUID,
    record_id: uuid.UUID,
    expected_locked_until: datetime,
    request_hash: str,
    status_code: int,
    body: bytes,
) -> None:
    try:
        ciphertext = _encrypt(body)
    except Exception:
        _release_claim(
            gym_id=gym_id,
            record_id=record_id,
            expected_locked_until=expected_locked_until,
            request_hash=request_hash,
        )
        return
    try:
        with transaction.atomic(durable=True):
            set_gym_context(gym_id=gym_id)
            record = IdempotencyRecord.objects.select_for_update().get(pk=record_id, gym_id=gym_id)
            if not _owns_claim(record, expected_locked_until, request_hash):
                return
            record.state = IdempotencyRecord.State.FAILED
            record.response_status = status_code
            record.response_body_ciphertext = ciphertext
            record.response_body_bytes = len(body)
            record.resource_type = None
            record.resource_id = None
            record.locked_until = None
            record.save(
                update_fields=[
                    "state",
                    "response_status",
                    "response_body_ciphertext",
                    "response_body_bytes",
                    "resource_type",
                    "resource_id",
                    "locked_until",
                    "updated_at",
                ]
            )
    except Exception:
        return


def _release_claim(
    *,
    gym_id: uuid.UUID,
    record_id: uuid.UUID,
    expected_locked_until: datetime,
    request_hash: str,
) -> None:
    """Drop this attempt's lease so a transient failure can be retried."""

    try:
        with transaction.atomic(durable=True):
            set_gym_context(gym_id=gym_id)
            record = IdempotencyRecord.objects.select_for_update().get(pk=record_id, gym_id=gym_id)
            if not _owns_claim(record, expected_locked_until, request_hash):
                return
            record.locked_until = timezone.now() - timedelta(seconds=1)
            record.save(update_fields=["locked_until", "updated_at"])
    except Exception:
        return


def _owns_claim(
    record: IdempotencyRecord,
    expected_locked_until: datetime,
    request_hash: str,
) -> bool:
    return (
        record.state == IdempotencyRecord.State.IN_PROGRESS
        and record.locked_until == expected_locked_until
        and hmac.compare_digest(record.request_hash, request_hash)
    )


def _replay(record: IdempotencyRecord) -> IdempotentResult:
    if (
        record.response_status is None
        or record.response_body_ciphertext is None
        or record.response_body_bytes is None
    ):
        raise IdempotencyInProgress()
    return IdempotentResult(
        replayed=True,
        status=record.response_status,
        body=_decrypt(record.response_body_ciphertext),
        resource_type=record.resource_type,
        resource_id=record.resource_id,
    )


def _validate_response(response: IdempotentResponse) -> None:
    if not isinstance(response, IdempotentResponse):
        raise IdempotencyRequestInvalid()
    if response.status < 200 or response.status > 299:
        raise RuntimeError("An idempotent finance command must return a 2xx response.")
    limit = settings.FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES
    if not isinstance(response.body, bytes) or not 1 <= len(response.body) <= limit:
        raise IdempotencyResponseTooLarge()
    has_type = response.resource_type is not None
    has_id = response.resource_id is not None
    if has_type != has_id:
        raise IdempotencyRequestInvalid()
    if (
        response.resource_type is not None
        and _RESOURCE_RE.fullmatch(response.resource_type) is None
    ):
        raise IdempotencyRequestInvalid()


def _client_failure_body(exc: BaseException, request: object | None) -> tuple[int, bytes] | None:
    """Serialize an explicitly cacheable finance-domain failure.

    Anything else, including validation, permission, authentication, and
    throttle errors, returns ``None`` so the claim is released. The body is
    the API handler envelope for ``request``, not a reconstruction from
    ``default_code`` or ``default_detail``.
    """

    if not getattr(exc, "cacheable_idempotent_failure", False) or not isinstance(exc, APIException):
        return None
    status_code = exc.status_code
    if not isinstance(status_code, int) or status_code < 400 or status_code > 499:
        return None
    response = api_exception_handler(exc, {"request": request})
    if response.status_code < 400 or response.status_code > 499:
        return None
    body = JSONRenderer().render(response.data)
    limit = settings.FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES
    if not isinstance(body, bytes) or not 1 <= len(body) <= limit:
        return None
    return response.status_code, body


def _encrypt(body: bytes) -> str:
    limit = settings.FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES
    if len(body) < 1 or len(body) > limit:
        raise IdempotencyResponseTooLarge()
    return _fernet().encrypt(body).decode()


def _decrypt(ciphertext: str) -> bytes:
    try:
        return _fernet().decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise RuntimeError("The stored idempotency response could not be read.") from exc


def _fernet() -> Fernet:
    material = settings.FINANCE_IDEMPOTENCY_ENCRYPTION_KEY
    if not isinstance(material, str) or not material:
        raise RuntimeError("FINANCE_IDEMPOTENCY_ENCRYPTION_KEY is not configured")
    mfa_key = settings.MFA_ENCRYPTION_KEY
    if isinstance(mfa_key, str) and mfa_key and material == mfa_key:
        raise RuntimeError("FINANCE_IDEMPOTENCY_ENCRYPTION_KEY must not match MFA_ENCRYPTION_KEY")
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def _sqlstate(exc: IntegrityError) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ("sqlstate", "pgcode"):
            code = getattr(current, attribute, None)
            if isinstance(code, str):
                return code
        current = current.__cause__ or current.__context__
    return None

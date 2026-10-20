from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core import signing
from django.http import QueryDict
from django.urls import resolve
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.common.api.schema import phase_1a_endpoints
from apps.members.models import Payment
from apps.members.serializers import (
    MemberPaymentHistoryItemSerializer,
    MemberPaymentHistoryQuerySerializer,
)
from apps.members.services import (
    MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
    MEMBER_PAYMENT_HISTORY_ORDERING,
    decode_member_payment_cursor,
    encode_member_payment_cursor,
    member_payment_history_scope,
)
from apps.members.views import MemberPaymentHistoryView, MemberSummaryView
from apps.users.permissions import (
    RequiresEntitlement,
    RequiresEntitlements,
    RequiresPermission,
    RequiresPermissions,
)

GYM_ID = uuid4()
MEMBER_ID = uuid4()
OTHER_MEMBER_ID = uuid4()
PAYMENT_ID = uuid4()
WHEN = datetime(1991, 3, 4, 5, 6, 7, tzinfo=UTC)
SECRET = "CURSOR-SECRET-VALUE"
SAFE_FIELDS = {
    "id",
    "membership_id",
    "original_payment_id",
    "entry_type",
    "purpose",
    "description",
    "amount",
    "currency",
    "method",
    "business_date",
    "received_at",
    "status",
    "receipt_number",
}


def _scope(*, gym_id=GYM_ID, member_id=MEMBER_ID, limit: int = 25) -> dict[str, str]:
    return member_payment_history_scope(gym_id=gym_id, member_id=member_id, limit=limit)


def _assert_invalid(token: str, scope: dict[str, str]) -> None:
    with pytest.raises(ValidationError) as caught:
        decode_member_payment_cursor(token, scope=scope)
    assert caught.value.detail == {"after": ["Invalid cursor."]}
    rendered = str(caught.value.detail)
    assert SECRET not in rendered
    assert "1991-03-04" not in rendered
    assert str(GYM_ID) not in rendered
    assert str(MEMBER_ID) not in rendered
    assert str(PAYMENT_ID) not in rendered


def test_payment_history_cursor_is_bound_hmac_signed_and_expires() -> None:
    scope = _scope(limit=25)
    issued_at = 1_700_000_000
    with patch("django.core.signing.time.time", return_value=issued_at):
        token = encode_member_payment_cursor(received_at=WHEN, payment_id=PAYMENT_ID, scope=scope)
    assert token.count(":") >= 2
    assert SECRET not in token

    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        assert decode_member_payment_cursor(token, scope=scope) == (WHEN, PAYMENT_ID)
        _assert_invalid(token, _scope(gym_id=uuid4(), limit=25))
        _assert_invalid(token, _scope(member_id=OTHER_MEMBER_ID, limit=25))
        _assert_invalid(token, _scope(limit=26))
        tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
        _assert_invalid(tampered, scope)
        forged = signing.dumps(
            {
                "received_at": WHEN.isoformat(),
                "id": str(PAYMENT_ID),
                **scope,
                "note": SECRET,
            },
            salt=MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
            compress=True,
        )
        _assert_invalid(forged, scope)
        reordered = signing.dumps(
            {
                "received_at": WHEN.isoformat(),
                "id": str(PAYMENT_ID),
                **{**scope, "ordering": "received_at ASC,id ASC"},
            },
            salt=MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
            compress=True,
        )
        _assert_invalid(reordered, scope)
        naive = signing.dumps(
            {
                "received_at": "1991-03-04T05:06:07",
                "id": str(PAYMENT_ID),
                **scope,
            },
            salt=MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
            compress=True,
        )
        _assert_invalid(naive, scope)

    with patch(
        "django.core.signing.time.time",
        return_value=issued_at + 24 * 60 * 60 + 5,
    ):
        _assert_invalid(token, scope)

    assert scope["ordering"] == MEMBER_PAYMENT_HISTORY_ORDERING
    assert scope["limit"] == "25"


def test_payment_history_index_matches_the_page_order() -> None:
    indexes = {index.name: list(index.fields) for index in Payment._meta.indexes}
    assert "payment_gym_member_idx" not in indexes
    assert indexes["payment_gym_member_hist_idx"] == [
        "gym",
        "member",
        "-received_at",
        "-id",
    ]
    assert len("payment_gym_member_hist_idx") <= 30


def test_payment_history_query_rejects_unknowns_and_bounds_limit() -> None:
    empty = MemberPaymentHistoryQuerySerializer(data={})
    limited = MemberPaymentHistoryQuerySerializer(data={"limit": "10", "after": "cursor"})
    gym_id = MemberPaymentHistoryQuerySerializer(data={"gym_id": str(GYM_ID)})
    unknown = MemberPaymentHistoryQuerySerializer(data={"status": "POSTED"})
    too_high = MemberPaymentHistoryQuerySerializer(data={"limit": "101"})
    too_low = MemberPaymentHistoryQuerySerializer(data={"limit": "0"})
    blank = MemberPaymentHistoryQuerySerializer(data={"after": ""})

    assert empty.is_valid(), empty.errors
    assert empty.validated_data["limit"] == 25
    assert "after" not in empty.validated_data
    assert limited.is_valid(), limited.errors
    assert limited.validated_data == {"limit": 10, "after": "cursor"}
    assert not gym_id.is_valid()
    assert str(gym_id.errors["gym_id"][0]) == "This field is not allowed."
    assert not unknown.is_valid()
    assert "status" in unknown.errors
    assert not too_high.is_valid()
    assert not too_low.is_valid()
    assert not blank.is_valid()
    assert "after" in blank.errors
    query_blank = MemberPaymentHistoryQuerySerializer(data=QueryDict("after="))
    query_space = MemberPaymentHistoryQuerySerializer(data=QueryDict("after=%20"))
    query_omitted = MemberPaymentHistoryQuerySerializer(data=QueryDict("limit=25"))
    assert not query_blank.is_valid()
    assert "after" in query_blank.errors
    assert not query_space.is_valid()
    assert "after" in query_space.errors
    assert query_omitted.is_valid(), query_omitted.errors
    assert "after" not in query_omitted.validated_data


def _payment(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": PAYMENT_ID,
        "membership_id": None,
        "original_payment_id": None,
        "entry_type": "CHARGE",
        "purpose": "MEMBERSHIP",
        "description": "May dues",
        "amount": Decimal("10.5"),
        "currency": "THB",
        "method": "CASH",
        "business_date": date(1991, 3, 4),
        "received_at": WHEN,
        "status": "DRAFT",
        "gym_id": GYM_ID,
        "member_id": MEMBER_ID,
        "member_name": SECRET,
        "reference_number": SECRET,
        "rejection_reason": SECRET,
        "created_by_membership_id": uuid4(),
        "created_at": WHEN,
        "updated_at": WHEN,
        "receipt": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_payment_history_item_exposes_only_safe_decimal_fields() -> None:
    receipt = SimpleNamespace(gym_id=GYM_ID, receipt_number="RCPT-100", issuer_name=SECRET)
    foreign = SimpleNamespace(gym_id=uuid4(), receipt_number="RCPT-FOREIGN", issuer_name=SECRET)
    membership_id = uuid4()
    original_id = uuid4()
    safe = MemberPaymentHistoryItemSerializer(
        _payment(
            membership_id=membership_id,
            original_payment_id=original_id,
            amount=Decimal("360"),
            status="POSTED",
            receipt=receipt,
        )
    ).data
    absent = MemberPaymentHistoryItemSerializer(_payment()).data
    hidden = MemberPaymentHistoryItemSerializer(_payment(receipt=foreign, status="VOID")).data

    assert set(safe) == SAFE_FIELDS
    assert safe["membership_id"] == str(membership_id)
    assert safe["original_payment_id"] == str(original_id)
    assert safe["amount"] == "360.00"
    assert safe["received_at"] == "1991-03-04T05:06:07Z"
    assert safe["business_date"] == "1991-03-04"
    assert safe["receipt_number"] == "RCPT-100"
    assert safe["status"] == "POSTED"
    assert absent["amount"] == "10.50"
    assert absent["membership_id"] is None
    assert absent["original_payment_id"] is None
    assert absent["receipt_number"] is None
    assert hidden["receipt_number"] is None
    rendered = f"{safe}{absent}{hidden}"
    assert SECRET not in rendered
    assert "issuer_name" not in safe
    assert "reference_number" not in safe


def test_payment_history_permissions_fail_closed_without_weakening_single_checks() -> None:
    class _Empty(APIView):
        required_permissions = ()
        required_features = ()
        required_permission = ""

    class _Both(APIView):
        required_permissions = ("members.view", "payments.view")

    class _One(APIView):
        required_permission = "members.view"

    granted = SimpleNamespace(
        permission_codes=frozenset({"members.view", "payments.view"}),
        gym=None,
    )
    partial = SimpleNamespace(permission_codes=frozenset({"members.view"}), gym=object())

    assert RequiresPermissions().has_permission(granted, _Empty()) is False
    assert RequiresEntitlements().has_permission(granted, _Empty()) is False
    assert RequiresPermissions().has_permission(granted, _Both()) is True
    assert RequiresPermissions().has_permission(partial, _Both()) is False
    assert RequiresPermission().has_permission(partial, _One()) is True
    assert RequiresPermission().has_permission(partial, _Empty()) is False
    assert MemberPaymentHistoryView.required_permissions == ("members.view", "payments.view")
    assert MemberPaymentHistoryView.required_features == ("members", "finance")
    assert RequiresPermissions in MemberPaymentHistoryView.permission_classes
    assert RequiresEntitlements in MemberPaymentHistoryView.permission_classes
    assert RequiresPermission not in MemberPaymentHistoryView.permission_classes
    assert RequiresEntitlement in MemberSummaryView.permission_classes
    assert RequiresPermission in MemberSummaryView.permission_classes


def test_schema_allowlist_publishes_nested_member_history() -> None:
    endpoints = [
        ("/api/v1/members/{id}/payments/", "", "GET", lambda: None),
        ("/api/v1/members/{id}/check-ins/", "", "GET", lambda: None),
        ("/api/v1/payments/", "", "GET", lambda: None),
        ("/api/v1/payments/{id}/refund/", "", "POST", lambda: None),
        ("/api/v1/receipts/", "", "GET", lambda: None),
        ("/api/v1/check-in-challenges/", "", "POST", lambda: None),
        ("/api/v1/check-ins/scan/", "", "POST", lambda: None),
        ("/api/v1/member/check-ins/scan/", "", "POST", lambda: None),
        ("/api/v1/trainer/check-ins/scan/", "", "POST", lambda: None),
    ]
    published = {path for path, *_rest in phase_1a_endpoints(endpoints)}

    assert published == {
        "/api/v1/members/{id}/payments/",
        "/api/v1/members/{id}/check-ins/",
        "/api/v1/payments/",
        "/api/v1/receipts/",
        "/api/v1/check-in-challenges/",
        "/api/v1/member/check-ins/scan/",
        "/api/v1/trainer/check-ins/scan/",
    }
    assert "/api/v1/payments/{id}/refund/" not in published
    payments = resolve(f"/api/v1/members/{MEMBER_ID}/payments/")
    check_ins = resolve(f"/api/v1/members/{MEMBER_ID}/check-ins/")
    assert payments.url_name == "member-payment-history"
    assert check_ins.url_name == "member-check-in-history"

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.exceptions import ValidationError

from apps.members.serializers import (
    MembershipCancelSerializer,
    MembershipCommandSerializer,
    MembershipCreateSerializer,
    MembershipExpiryQuerySerializer,
    MembershipListQuerySerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
)
from apps.members.services import (
    decode_expiry_cursor,
    decode_membership_cursor,
    encode_expiry_cursor,
    encode_membership_cursor,
    inclusive_expiry_date,
)

WRITE_FIELDS = {"member_id", "plan_id", "start_date", "discount_amount", "note"}
READ_FIELDS = {
    "id",
    "member_id",
    "plan_id",
    "plan_code",
    "plan_name",
    "duration_value",
    "duration_unit",
    "start_date",
    "expiry_date",
    "list_price",
    "discount_amount",
    "final_amount",
    "currency",
    "payment_status",
    "status",
    "note",
    "cancelled_at",
    "cancelled_by_membership_id",
    "cancellation_reason",
    "created_by_membership_id",
    "created_at",
    "updated_at",
}
MEMBERSHIP_ID = "7df4f2d6-79d3-4fd1-b850-968142b1b6dd"


def _create_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "member_id": MEMBERSHIP_ID,
        "plan_id": MEMBERSHIP_ID,
        "start_date": "2026-10-17",
        "discount_amount": "10.00",
        "note": "Front desk note",
    }
    body.update(overrides)
    return body


def test_membership_create_allowlist_rejects_server_fields() -> None:
    rejected = MembershipCreateSerializer(
        data=_create_body(
            gym_id=MEMBERSHIP_ID,
            id=MEMBERSHIP_ID,
            plan_code="MONTH",
            plan_name="Monthly",
            duration_value=1,
            duration_unit="MONTH",
            expiry_date="2026-11-16",
            list_price="1500.00",
            final_amount="1490.00",
            currency="USD",
            payment_status="PAID",
            status="ACTIVE",
            cancelled_at="2026-10-17T00:00:00Z",
            cancelled_by_membership_id=MEMBERSHIP_ID,
            cancellation_reason="No",
            created_by_membership_id=MEMBERSHIP_ID,
            created_at="2026-10-17T00:00:00Z",
            updated_at="2026-10-17T00:00:00Z",
            revenue_source="NEW_MEMBER",
        )
    )
    accepted = MembershipCreateSerializer(data=_create_body())

    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "gym_id",
        "id",
        "plan_code",
        "plan_name",
        "duration_value",
        "duration_unit",
        "expiry_date",
        "list_price",
        "final_amount",
        "currency",
        "payment_status",
        "status",
        "cancelled_at",
        "cancelled_by_membership_id",
        "cancellation_reason",
        "created_by_membership_id",
        "created_at",
        "updated_at",
        "revenue_source",
    }
    assert accepted.is_valid(), accepted.errors
    assert set(accepted.validated_data) == WRITE_FIELDS
    assert accepted.validated_data["discount_amount"] == Decimal("10.00")
    assert accepted.validated_data["note"] == "Front desk note"


def test_membership_create_money_and_note_rules() -> None:
    omitted = MembershipCreateSerializer(
        data={"member_id": MEMBERSHIP_ID, "plan_id": MEMBERSHIP_ID, "start_date": "2026-10-17"}
    )
    negative = MembershipCreateSerializer(data=_create_body(discount_amount="-0.01"))
    scale = MembershipCreateSerializer(data=_create_body(discount_amount="1.129"))
    floated = MembershipCreateSerializer(data=_create_body(discount_amount=10.5))
    blank_note = MembershipCreateSerializer(data=_create_body(note="   "))

    assert omitted.is_valid(), omitted.errors
    assert omitted.validated_data["discount_amount"] == Decimal("0")
    assert omitted.validated_data["note"] == ""
    assert not negative.is_valid()
    assert "discount_amount" in negative.errors
    assert not scale.is_valid()
    assert "discount_amount" in scale.errors
    assert not floated.is_valid()
    assert "discount_amount" in floated.errors
    assert blank_note.is_valid(), blank_note.errors
    assert blank_note.validated_data["note"] == ""


def test_membership_update_accepts_only_a_note() -> None:
    empty = MembershipUpdateSerializer(data={})
    forged = MembershipUpdateSerializer(data={"note": "Updated", "status": "CANCELLED"})
    cleared = MembershipUpdateSerializer(data={"note": "  keep me  "})

    assert not empty.is_valid()
    assert "note" in empty.errors
    assert not forged.is_valid()
    assert "status" in forged.errors
    assert cleared.is_valid(), cleared.errors
    assert cleared.validated_data == {"note": "keep me"}


def test_membership_commands_reject_client_state() -> None:
    blank = MembershipCancelSerializer(data={"reason": "   "})
    extra = MembershipCancelSerializer(data={"reason": "Moved away", "status": "CANCELLED"})
    accepted = MembershipCancelSerializer(data={"reason": "  Moved away  "})
    suspend = MembershipCommandSerializer(data={"status": "SUSPENDED"})
    empty_command = MembershipCommandSerializer(data={})

    assert not blank.is_valid()
    assert "reason" in blank.errors
    assert not extra.is_valid()
    assert "status" in extra.errors
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["reason"] == "Moved away"
    assert not suspend.is_valid()
    assert empty_command.is_valid(), empty_command.errors


def test_membership_queries_are_allowlisted() -> None:
    listed = MembershipListQuerySerializer(
        data={"gym_id": MEMBERSHIP_ID, "bucket": "today", "search": "ada"}
    )
    expiry = MembershipExpiryQuerySerializer(data={"status": "ACTIVE"})
    missing_bucket = MembershipExpiryQuerySerializer(data={})
    accepted = MembershipListQuerySerializer(data={})
    bucket = MembershipExpiryQuerySerializer(data={"bucket": "within_7_days", "limit": 10})

    assert not listed.is_valid()
    assert set(listed.errors) == {"gym_id", "bucket", "search"}
    assert not expiry.is_valid()
    assert set(expiry.errors) == {"status"}
    assert not missing_bucket.is_valid()
    assert "bucket" in missing_bucket.errors
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["limit"] == 25
    assert bucket.is_valid(), bucket.errors
    assert bucket.validated_data["bucket"] == "within_7_days"


def test_membership_response_contract_is_explicit() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    payload = MembershipSerializer(
        SimpleNamespace(
            id=uuid4(),
            member_id=uuid4(),
            plan_id=uuid4(),
            plan_code="MONTH",
            plan_name="Monthly",
            duration_value=1,
            duration_unit="MONTH",
            start_date=date(2026, 10, 1),
            expiry_date=date(2026, 10, 31),
            list_price=Decimal("1500.00"),
            discount_amount=Decimal("100.00"),
            final_amount=Decimal("1400.00"),
            currency="THB",
            payment_status="UNPAID",
            status="ACTIVE",
            note="",
            cancelled_at=None,
            cancelled_by_membership_id=None,
            cancellation_reason="",
            created_by_membership_id=uuid4(),
            created_at=now,
            updated_at=now,
            gym_id=uuid4(),
            revenue_source="NEW_MEMBER",
        )
    ).data

    assert set(payload) == READ_FIELDS
    assert payload["list_price"] == "1500.00"
    assert payload["final_amount"] == "1400.00"
    assert payload["currency"] == "THB"
    assert "gym_id" not in payload
    assert "revenue_source" not in payload
    assert "paid_amount" not in payload


@pytest.mark.parametrize(
    ("start", "value", "unit", "expiry"),
    [
        (date(2026, 1, 15), 1, "DAY", date(2026, 1, 15)),
        (date(2026, 1, 15), 7, "DAY", date(2026, 1, 21)),
        (date(2026, 1, 5), 1, "WEEK", date(2026, 1, 11)),
        (date(2026, 1, 1), 1, "MONTH", date(2026, 1, 31)),
        (date(2026, 1, 31), 1, "MONTH", date(2026, 2, 27)),
        (date(2024, 1, 31), 1, "MONTH", date(2024, 2, 28)),
        (date(2024, 2, 29), 1, "YEAR", date(2025, 2, 27)),
        (date(2026, 2, 28), 1, "YEAR", date(2027, 2, 27)),
        (date(2026, 3, 31), 1, "MONTH", date(2026, 4, 29)),
    ],
)
def test_inclusive_expiry_handles_calendar_edges(
    start: date, value: int, unit: str, expiry: date
) -> None:
    assert inclusive_expiry_date(start, value, unit) == expiry


def test_membership_cursors_are_bound_to_gym_filter_and_order() -> None:
    gym_id = uuid4()
    member_id = uuid4()
    membership_id = uuid4()
    scope = {
        "gym_id": str(gym_id),
        "status": "ACTIVE",
        "member_id": str(member_id),
        "payment_status": "",
        "ordering": "start_date DESC,id DESC",
    }
    token = encode_membership_cursor(
        start_date=date(2026, 10, 17), membership_id=membership_id, scope=scope
    )
    expiry_scope = {
        "gym_id": str(gym_id),
        "bucket": "within_7_days",
        "ordering": "expiry_date ASC,id ASC",
    }
    expiry_token = encode_expiry_cursor(
        expiry_date=date(2026, 10, 20), membership_id=membership_id, scope=expiry_scope
    )

    assert decode_membership_cursor(token, scope=scope) == (date(2026, 10, 17), membership_id)
    assert decode_expiry_cursor(expiry_token, scope=expiry_scope) == (
        date(2026, 10, 20),
        membership_id,
    )
    other_gym = {**scope, "gym_id": str(uuid4())}
    other_filter = {**scope, "status": "PENDING"}
    other_order = {**scope, "ordering": "expiry_date ASC,id ASC"}
    other_bucket = {**expiry_scope, "bucket": "today"}
    with pytest.raises(ValidationError):
        decode_membership_cursor(token, scope=other_gym)
    with pytest.raises(ValidationError):
        decode_membership_cursor(token, scope=other_filter)
    with pytest.raises(ValidationError):
        decode_membership_cursor(token, scope=other_order)
    with pytest.raises(ValidationError):
        decode_expiry_cursor(expiry_token, scope=other_bucket)
    with pytest.raises(ValidationError):
        decode_expiry_cursor(token, scope=expiry_scope)

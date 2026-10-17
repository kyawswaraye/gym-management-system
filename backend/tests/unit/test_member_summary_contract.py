from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from apps.members.serializers import (
    MemberSerializer,
    MemberSummaryQuerySerializer,
    MemberSummarySerializer,
    PaymentSummarySerializer,
    member_is_pt_client,
)
from apps.members.services import inclusive_days_remaining

MEMBER_ID = UUID("7df4f2d6-79d3-4fd1-b850-968142b1b6dd")
MEMBERSHIP_ID = UUID("8df4f2d6-79d3-4fd1-b850-968142b1b6dd")
PLAN_ID = UUID("9df4f2d6-79d3-4fd1-b850-968142b1b6dd")
ACTOR_ID = UUID("adf4f2d6-79d3-4fd1-b850-968142b1b6dd")
WHEN = datetime(2026, 10, 17, tzinfo=UTC)
SUMMARY_FIELDS = {
    "member",
    "current_membership",
    "days_remaining",
    "is_pt_client",
    "membership_history",
    "membership_history_has_more",
    "payment_summary",
    "check_in_summary",
}
PAYMENT_SUMMARY_FIELDS = {
    "available",
    "currency",
    "total_charged",
    "total_refunded",
    "net_paid",
    "outstanding_membership_balance",
}
FORBIDDEN_PAYMENT_FIELDS = {
    "id",
    "receipt",
    "receipt_number",
    "reference",
    "reference_number",
    "description",
    "method",
    "entry_type",
    "received_at",
    "business_date",
    "approved_by_membership_id",
    "created_by_membership_id",
    "payment_history",
}


def test_inclusive_days_remaining_counts_today_and_floors_at_zero() -> None:
    today = date(2026, 10, 17)

    assert inclusive_days_remaining(today, today) == 1
    assert inclusive_days_remaining(date(2026, 10, 19), today) == 3
    assert inclusive_days_remaining(date(2026, 10, 16), today) == 0


def test_summary_is_pt_client_matches_the_member_serializer() -> None:
    member = SimpleNamespace()

    assert member_is_pt_client(member) is False
    assert MemberSerializer().get_is_pt_client(member) is False
    assert MemberSummarySerializer().get_is_pt_client(
        {"member": member}
    ) is MemberSerializer().get_is_pt_client(member)


def test_member_summary_rejects_every_query_parameter() -> None:
    empty = MemberSummaryQuerySerializer(data={})
    gym_id = MemberSummaryQuerySerializer(data={"gym_id": str(MEMBER_ID)})
    limit = MemberSummaryQuerySerializer(data={"limit": "20"})
    blank = MemberSummaryQuerySerializer(data={"gym_id": ""})

    assert empty.is_valid(), empty.errors
    assert empty.validated_data == {}
    assert not gym_id.is_valid()
    assert str(gym_id.errors["gym_id"][0]) == "This field is not allowed."
    assert not limit.is_valid()
    assert "limit" in limit.errors
    assert not blank.is_valid()
    assert "gym_id" in blank.errors


def test_payment_summary_serializes_exact_amounts_without_ledger_fields() -> None:
    payload = PaymentSummarySerializer(
        {
            "available": True,
            "currency": "THB",
            "total_charged": Decimal("360"),
            "total_refunded": Decimal("432.1"),
            "net_paid": Decimal("0"),
            "outstanding_membership_balance": Decimal("97.006"),
        }
    ).data

    assert set(payload) == PAYMENT_SUMMARY_FIELDS
    assert payload["available"] is True
    assert payload["currency"] == "THB"
    assert payload["total_charged"] == "360.00"
    assert payload["total_refunded"] == "432.10"
    assert payload["net_paid"] == "0.00"
    assert payload["outstanding_membership_balance"] == "97.01"
    assert FORBIDDEN_PAYMENT_FIELDS.isdisjoint(payload)


def _member() -> SimpleNamespace:
    return SimpleNamespace(
        id=MEMBER_ID,
        member_number="MEM-000001",
        first_name="Ada",
        last_name="Lovelace",
        normalized_full_name="ada lovelace",
        phone=None,
        email=None,
        gender=None,
        date_of_birth=None,
        address={},
        join_date=date(2026, 10, 1),
        emergency_contact_name="",
        emergency_contact_phone="",
        notes="",
        profile_photo_asset_id=None,
        linked_user_id=None,
        authz_version=1,
        status="ACTIVE",
        archived_at=None,
        created_at=WHEN,
        updated_at=WHEN,
    )


def _membership() -> SimpleNamespace:
    return SimpleNamespace(
        id=MEMBERSHIP_ID,
        member_id=MEMBER_ID,
        plan_id=PLAN_ID,
        plan_code="MONTH",
        plan_name="Monthly",
        duration_value=1,
        duration_unit="MONTH",
        start_date=date(2026, 10, 17),
        expiry_date=date(2026, 11, 16),
        list_price=Decimal("1500.00"),
        discount_amount=Decimal("0.00"),
        final_amount=Decimal("1500.00"),
        currency="THB",
        payment_status="UNPAID",
        status="ACTIVE",
        note="",
        cancelled_at=None,
        cancelled_by_membership_id=None,
        cancellation_reason="",
        created_by_membership_id=ACTOR_ID,
        created_at=WHEN,
        updated_at=WHEN,
    )


def test_member_summary_serializer_uses_the_explicit_contract() -> None:
    member = _member()
    rendered = MemberSummarySerializer(
        {
            "member": member,
            "current_membership": _membership(),
            "days_remaining": 1,
            "membership_history": [_membership()],
            "membership_history_has_more": False,
            "payment_summary": {
                "available": True,
                "currency": "THB",
                "total_charged": Decimal("10.00"),
                "total_refunded": Decimal("0.00"),
                "net_paid": Decimal("10.00"),
                "outstanding_membership_balance": Decimal("0.00"),
            },
            "check_in_summary": {
                "available": True,
                "currently_checked_in": False,
                "open_check_in_id": None,
                "last_check_in_at": None,
                "last_check_out_at": None,
            },
        }
    ).data

    assert set(rendered) == SUMMARY_FIELDS
    assert rendered["is_pt_client"] is False
    assert rendered["member"]["is_pt_client"] is False
    assert rendered["member"]["id"] == str(MEMBER_ID)
    assert "gym_id" not in rendered["member"]
    assert rendered["current_membership"]["id"] == str(MEMBERSHIP_ID)
    assert rendered["days_remaining"] == 1
    assert rendered["membership_history_has_more"] is False
    assert len(rendered["membership_history"]) == 1
    assert set(rendered["payment_summary"]) == PAYMENT_SUMMARY_FIELDS
    assert rendered["payment_summary"]["total_charged"] == "10.00"
    assert rendered["check_in_summary"] == {
        "available": True,
        "currently_checked_in": False,
        "open_check_in_id": None,
        "last_check_in_at": None,
        "last_check_out_at": None,
    }
    assert "payment_history" not in rendered
    assert "receipt_number" not in rendered["payment_summary"]

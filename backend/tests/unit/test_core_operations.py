from datetime import date

from apps.members.models import MembershipPlan
from apps.members.serializers import MemberCreateSerializer, PaymentCreateSerializer
from apps.members.services import _add_duration


def test_calendar_membership_expiry_handles_month_end_and_leap_year() -> None:
    assert _add_duration(date(2027, 1, 31), 1, MembershipPlan.DurationUnit.MONTH) == date(
        2027, 2, 27
    )
    assert _add_duration(date(2028, 2, 29), 1, MembershipPlan.DurationUnit.YEAR) == date(
        2029, 2, 27
    )


def test_member_input_rejects_tenant_and_status_mass_assignment() -> None:
    serializer = MemberCreateSerializer(
        data={
            "first_name": "Aye",
            "join_date": "2026-09-10",
            "gym_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "status": "ACTIVE",
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"gym_id", "status"}


def test_payment_input_rejects_server_owned_status_and_totals() -> None:
    serializer = PaymentCreateSerializer(
        data={
            "member_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "purpose": "MEMBERSHIP",
            "description": "Monthly membership",
            "amount": "500.00",
            "currency": "THB",
            "method": "CASH",
            "business_date": "2026-09-10",
            "received_at": "2026-09-10T10:00:00Z",
            "status": "POSTED",
            "gym_id": "c131004c-1948-4717-b310-c0afc28bb36c",
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"gym_id", "status"}

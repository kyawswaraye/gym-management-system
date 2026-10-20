"""Serializer and preview contract for draft PT sales."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from apps.trainers.pt_sale_services import preview_sale_amounts
from apps.trainers.serializers import (
    PTCommissionListQuerySerializer,
    PTCommissionPaymentWriteSerializer,
    PTSaleListQuerySerializer,
    PTSaleSerializer,
    PTSaleUpdateSerializer,
    PTSaleWriteSerializer,
)

_ACTOR_FIELDS = (
    "created_by_membership_id",
    "updated_by_membership_id",
    "submitted_by_membership_id",
    "approved_by_membership_id",
    "rejected_by_membership_id",
    "cancelled_by_membership_id",
)

_VALID = {
    "member_id": "11111111-1111-1111-1111-111111111111",
    "trainer_staff_profile_id": "22222222-2222-2222-2222-222222222222",
    "service_date": "2026-10-19",
    "description": "Private session",
    "gross_fee": "10.00",
    "currency": "THB",
}


def test_pt_sale_write_accepts_only_public_fields() -> None:
    serializer = PTSaleWriteSerializer(data=_VALID)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["gross_fee"] == Decimal("10.00")
    assert set(serializer.validated_data) == set(_VALID)


def test_pt_sale_write_rejects_server_fields() -> None:
    payload = {
        **_VALID,
        "gym_id": str(uuid4()),
        "status": "POSTED",
        "commission_percent_snapshot": "40.00",
        "commission_amount": "4.00",
        "paid_amount": "1.00",
        "payment_id": str(uuid4()),
        "created_by_membership_id": str(uuid4()),
        "updated_by_membership_id": str(uuid4()),
        "submitted_by_membership_id": str(uuid4()),
        "approved_by_membership_id": str(uuid4()),
        "rejected_by_membership_id": str(uuid4()),
        "cancelled_by_membership_id": str(uuid4()),
    }
    serializer = PTSaleWriteSerializer(data=payload)
    assert not serializer.is_valid()
    for field in (
        "gym_id",
        "status",
        "commission_percent_snapshot",
        "commission_amount",
        "paid_amount",
        "payment_id",
        *_ACTOR_FIELDS,
    ):
        assert field in serializer.errors
    update = PTSaleUpdateSerializer(data={name: str(uuid4()) for name in _ACTOR_FIELDS})
    assert not update.is_valid()
    assert set(_ACTOR_FIELDS) <= set(update.errors)


def test_pt_sale_payment_draft_accepts_only_sale_owned_fields() -> None:
    draft = {
        "description": "Session fee",
        "amount": "10.00",
        "method": "CASH",
        "business_date": "2026-10-19",
    }
    accepted = PTSaleWriteSerializer(data={**_VALID, "payment": draft})
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["payment"]["amount"] == Decimal("10.00")
    stolen = PTSaleWriteSerializer(
        data={
            **_VALID,
            "payment": {
                **draft,
                "purpose": "PT",
                "member_id": str(uuid4()),
                "membership_id": str(uuid4()),
                "status": "POSTED",
                "currency": "THB",
            },
        }
    )
    assert not stolen.is_valid()
    assert "payment" in stolen.errors
    removed = PTSaleUpdateSerializer(data={"payment": None})
    assert removed.is_valid(), removed.errors
    assert removed.validated_data["payment"] is None
    omitted = PTSaleUpdateSerializer(data={"description": "Changed"})
    assert omitted.is_valid(), omitted.errors
    assert "payment" not in omitted.validated_data


def test_pt_sale_fee_rejects_extra_precision_and_zero() -> None:
    extra = PTSaleWriteSerializer(data={**_VALID, "gross_fee": "10.999"})
    assert not extra.is_valid()
    zero = PTSaleWriteSerializer(data={**_VALID, "gross_fee": "0.00"})
    assert not zero.is_valid()
    negative = PTSaleWriteSerializer(data={**_VALID, "gross_fee": "-1.00"})
    assert not negative.is_valid()
    floated = PTSaleWriteSerializer(data={**_VALID, "gross_fee": 10.5})
    assert not floated.is_valid()


def test_pt_sale_update_requires_a_field() -> None:
    empty = PTSaleUpdateSerializer(data={})
    assert not empty.is_valid()
    described = PTSaleUpdateSerializer(data={"description": "Changed"})
    assert described.is_valid(), described.errors


def test_pt_sale_response_exposes_read_only_actor_membership_ids() -> None:
    assert set(_ACTOR_FIELDS) <= set(PTSaleSerializer().fields)
    nullable = {
        "submitted_by_membership_id",
        "approved_by_membership_id",
        "rejected_by_membership_id",
        "cancelled_by_membership_id",
    }
    for name in nullable:
        assert PTSaleSerializer().fields[name].allow_null is True
    assert PTSaleSerializer().fields["created_by_membership_id"].allow_null is False
    assert PTSaleSerializer().fields["updated_by_membership_id"].allow_null is False


def test_pt_sale_list_rejects_an_inverted_service_date_range() -> None:
    inverted = PTSaleListQuerySerializer(
        data={"service_date_from": "2026-10-19", "service_date_to": "2026-10-18"}
    )
    assert not inverted.is_valid()
    assert "service_date_to" in inverted.errors
    same_day = PTSaleListQuerySerializer(
        data={"service_date_from": "2026-10-19", "service_date_to": "2026-10-19"}
    )
    assert same_day.is_valid(), same_day.errors
    open_end = PTSaleListQuerySerializer(data={"service_date_from": "2026-10-19"})
    assert open_end.is_valid(), open_end.errors


def test_preview_rounds_half_up_at_percent_edges() -> None:
    half = preview_sale_amounts(gross_fee=Decimal("0.01"), percent=Decimal("50.00"))
    assert half == (Decimal("0.01"), Decimal("0.00"), Decimal("0.00"), Decimal("0.01"))
    third = preview_sale_amounts(gross_fee=Decimal("10.00"), percent=Decimal("33.33"))
    assert third == (Decimal("3.33"), Decimal("6.67"), Decimal("0.00"), Decimal("3.33"))
    full = preview_sale_amounts(gross_fee=Decimal("80.00"), percent=Decimal("100.00"))
    assert full == (Decimal("80.00"), Decimal("0.00"), Decimal("0.00"), Decimal("80.00"))
    none = preview_sale_amounts(gross_fee=Decimal("80.00"), percent=Decimal("0.00"))
    assert none == (Decimal("0.00"), Decimal("80.00"), Decimal("0.00"), Decimal("0.00"))


def test_commission_payout_accepts_only_payment_fields() -> None:
    payload = {
        "amount": "1.00",
        "business_date": "2026-10-19",
        "payment_method": "BANK_TRANSFER",
        "reference_number": "EXT-1",
        "policy_note": "October payout",
    }
    accepted = PTCommissionPaymentWriteSerializer(data=payload)
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["amount"] == Decimal("1.00")
    rejected = PTCommissionPaymentWriteSerializer(
        data={
            **payload,
            "commission_amount": "3.33",
            "status": "PAID",
            "expense_id": "33333333-3333-3333-3333-333333333333",
            "created_by_membership_id": "44444444-4444-4444-4444-444444444444",
        }
    )
    assert not rejected.is_valid()
    zero = PTCommissionPaymentWriteSerializer(data={**payload, "amount": "0.00"})
    assert not zero.is_valid()
    assert "amount" in zero.errors
    extra_places = PTCommissionPaymentWriteSerializer(data={**payload, "amount": "1.001"})
    assert not extra_places.is_valid()
    inverted = PTCommissionListQuerySerializer(
        data={"service_date_from": "2026-10-19", "service_date_to": "2026-10-18"}
    )
    assert not inverted.is_valid()
    assert "service_date_to" in inverted.errors

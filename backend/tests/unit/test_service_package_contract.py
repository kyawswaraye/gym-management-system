"""Serializer contract for the PT service-package catalog."""

from __future__ import annotations

from decimal import Decimal

from apps.trainers.serializers import ServicePackageWriteSerializer

_VALID = {
    "type": "PT",
    "code": "PT_10",
    "name": "Ten sessions",
    "session_count": 10,
    "validity_value": 3,
    "validity_unit": "MONTH",
    "price": "1500.00",
}


def test_service_package_write_accepts_only_public_fields() -> None:
    serializer = ServicePackageWriteSerializer(data=_VALID)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["price"] == Decimal("1500.00")
    assert set(serializer.validated_data) == set(_VALID)


def test_service_package_write_rejects_server_owned_fields() -> None:
    payload = {
        **_VALID,
        "gym_id": "11111111-1111-1111-1111-111111111111",
        "currency": "USD",
        "is_active": False,
        "created_by_membership_id": "33333333-3333-3333-3333-333333333333",
        "normalized_name": "TEN SESSIONS",
    }
    serializer = ServicePackageWriteSerializer(data=payload)
    assert not serializer.is_valid()
    for field in (
        "gym_id",
        "currency",
        "is_active",
        "created_by_membership_id",
        "normalized_name",
    ):
        assert field in serializer.errors


def test_service_package_price_rejects_extra_precision_and_floats() -> None:
    extra = ServicePackageWriteSerializer(data={**_VALID, "price": "10.999"})
    assert not extra.is_valid()
    assert "price" in extra.errors
    scientific = ServicePackageWriteSerializer(data={**_VALID, "price": "1e2"})
    assert not scientific.is_valid()
    floated = ServicePackageWriteSerializer(data={**_VALID, "price": 10.5})
    assert not floated.is_valid()
    zero = ServicePackageWriteSerializer(data={**_VALID, "price": "0.00"})
    assert zero.is_valid(), zero.errors
    negative = ServicePackageWriteSerializer(data={**_VALID, "price": "-0.01"})
    assert not negative.is_valid()


def test_service_package_code_rejects_lowercase() -> None:
    serializer = ServicePackageWriteSerializer(data={**_VALID, "code": "pt_10"})
    assert not serializer.is_valid()
    assert "code" in serializer.errors


def test_service_package_class_type_is_required_for_class_and_rejected_for_pt() -> None:
    missing = ServicePackageWriteSerializer(data={**_VALID, "type": "CLASS"})
    assert not missing.is_valid()
    assert "class_type_id" in missing.errors
    linked = ServicePackageWriteSerializer(
        data={
            **_VALID,
            "type": "CLASS",
            "class_type_id": "22222222-2222-2222-2222-222222222222",
        }
    )
    assert linked.is_valid(), linked.errors
    rejected = ServicePackageWriteSerializer(
        data={**_VALID, "class_type_id": "22222222-2222-2222-2222-222222222222"}
    )
    assert not rejected.is_valid()
    assert "class_type_id" in rejected.errors

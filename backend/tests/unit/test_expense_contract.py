from decimal import Decimal

from apps.expenses.serializers import (
    ExpenseCategoryListQuerySerializer,
    ExpenseCategoryWriteSerializer,
    ExpenseWriteSerializer,
    VoidExpenseSerializer,
)
from apps.files.serializers import UploadIntentSerializer


def test_expense_write_rejects_mass_assignment_and_inexact_money() -> None:
    rejected = ExpenseWriteSerializer(
        data={
            "category_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "business_date": "2026-10-18",
            "description": "Rent",
            "amount": 10,
            "currency": "THB",
            "payment_method": "CASH",
            "gym_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "status": "POSTED",
        }
    )
    assert not rejected.is_valid()
    assert "gym_id" in rejected.errors or "amount" in rejected.errors or "status" in rejected.errors

    fuzzy = ExpenseWriteSerializer(
        data={
            "category_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "business_date": "2026-10-18",
            "description": "Rent",
            "amount": "10.0",
            "currency": "thb",
            "payment_method": "CASH",
        }
    )
    assert not fuzzy.is_valid()

    exact = ExpenseWriteSerializer(
        data={
            "category_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "business_date": "2026-10-18",
            "description": "Rent",
            "amount": "10.00",
            "currency": "THB",
            "payment_method": "CASH",
            "policy_note": "Owner approved vendor",
        }
    )
    assert exact.is_valid(), exact.errors
    assert exact.validated_data["amount"] == Decimal("10.00")


def test_expense_void_rejects_mass_assignment_and_blank_reason() -> None:
    rejected = VoidExpenseSerializer(data={"reason": " ", "status": "VOID", "amount": "1.00"})
    assert not rejected.is_valid()
    assert "reason" in rejected.errors or "status" in rejected.errors
    accepted = VoidExpenseSerializer(data={"reason": "Entered twice"})
    assert accepted.is_valid(), accepted.errors


def test_upload_intent_accepts_expense_receipt_gym_owner() -> None:
    photo = UploadIntentSerializer(
        data={
            "original_name": "bill.jpg",
            "content_type": "image/jpeg",
            "byte_size": 12,
            "sha256": "a" * 64,
            "purpose": "EXPENSE_RECEIPT",
            "owner_type": "MEMBER",
            "owner_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
        }
    )
    assert not photo.is_valid()
    allowed = UploadIntentSerializer(
        data={
            "original_name": "bill.jpg",
            "content_type": "image/jpeg",
            "byte_size": 12,
            "sha256": "a" * 64,
            "purpose": "EXPENSE_RECEIPT",
            "owner_type": "GYM",
            "owner_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
        }
    )
    assert allowed.is_valid(), allowed.errors
    still_photo_only_mismatch = UploadIntentSerializer(
        data={
            "original_name": "photo.jpg",
            "content_type": "image/jpeg",
            "byte_size": 12,
            "sha256": "a" * 64,
            "purpose": "PROFILE_PHOTO",
            "owner_type": "GYM",
            "owner_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
        }
    )
    assert not still_photo_only_mismatch.is_valid()


def test_category_list_query_omits_is_active_instead_of_defaulting_false() -> None:
    omitted = ExpenseCategoryListQuerySerializer(data={})
    assert omitted.is_valid(), omitted.errors
    assert "is_active" not in omitted.validated_data
    active = ExpenseCategoryListQuerySerializer(data={"is_active": "true"})
    assert active.is_valid(), active.errors
    assert active.validated_data["is_active"] is True


def test_custom_category_rejects_lowercase_code() -> None:
    serializer = ExpenseCategoryWriteSerializer(data={"code": "rent", "name": "Shop rent"})
    assert not serializer.is_valid()
    assert "code" in serializer.errors

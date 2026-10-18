from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.expenses.views import ExpenseVoidView
from apps.members.payment_serializers import (
    PaymentWriteSerializer,
    RefundPaymentSerializer,
    VoidPaymentSerializer,
    VoidReceiptSerializer,
)
from apps.members.payment_views import PaymentRefundView, PaymentVoidView, ReceiptVoidView
from apps.members.receipt_rendering import receipt_download_response, render_receipt_html


def test_payment_write_rejects_inexact_money_and_unsupported_purpose() -> None:
    rejected = PaymentWriteSerializer(
        data={
            "purpose": "PT",
            "description": "Session",
            "amount": "10.00",
            "currency": "THB",
            "method": "CASH",
            "business_date": "2026-10-18",
        }
    )
    assert not rejected.is_valid()
    assert "purpose" in rejected.errors

    fuzzy = PaymentWriteSerializer(
        data={
            "purpose": "OTHER",
            "description": "Walk in",
            "amount": 10,
            "currency": "THB",
            "method": "CASH",
            "business_date": "2026-10-18",
            "gym_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
        }
    )
    assert not fuzzy.is_valid()
    assert "amount" in fuzzy.errors or "gym_id" in fuzzy.errors

    exact = PaymentWriteSerializer(
        data={
            "purpose": "OTHER",
            "description": "Walk in",
            "amount": "10.00",
            "currency": "thb",
            "method": "CASH",
            "business_date": "2026-10-18",
        }
    )
    assert not exact.is_valid()
    assert "currency" in exact.errors


def test_correction_serializers_reject_mass_assignment_and_blank_reason() -> None:
    blank = VoidPaymentSerializer(data={"reason": "   ", "gym_id": "x", "status": "VOID"})
    assert not blank.is_valid()
    assert "reason" in blank.errors or "gym_id" in blank.errors

    accepted = VoidPaymentSerializer(data={"reason": "Duplicate charge"})
    assert accepted.is_valid(), accepted.errors
    assert "cash_session_id" not in accepted.validated_data

    refund = RefundPaymentSerializer(
        data={
            "amount": "10.00",
            "reason": "Member request",
            "method": "BANK_TRANSFER",
            "business_date": "2026-10-18",
            "entry_type": "REFUND",
        }
    )
    assert not refund.is_valid()
    assert "entry_type" in refund.errors

    fuzzy = RefundPaymentSerializer(
        data={
            "amount": 10,
            "reason": "Member request",
            "method": "BANK_TRANSFER",
            "business_date": "2026-10-18",
        }
    )
    assert not fuzzy.is_valid()
    assert "amount" in fuzzy.errors

    receipt = VoidReceiptSerializer(data={"reason": "Wrong copy", "payment_id": "x"})
    assert not receipt.is_valid()
    assert "payment_id" in receipt.errors


def test_correction_commands_opt_out_of_atomic_requests() -> None:
    assert "default" in PaymentVoidView.dispatch._non_atomic_requests
    assert "default" in PaymentRefundView.dispatch._non_atomic_requests
    assert "default" in ReceiptVoidView.dispatch._non_atomic_requests
    assert "default" in ExpenseVoidView.dispatch._non_atomic_requests


def test_receipt_html_escapes_values_and_sets_download_headers() -> None:
    receipt = SimpleNamespace(
        receipt_number='RCPT-1"><script>',
        issued_at=datetime(2026, 10, 18, 1, 2, tzinfo=UTC),
        gym_code="GYM",
        gym_name="Gym <Name>",
        gym_address="1 Main",
        member_number="Walk-in",
        member_name="Walk-in",
        description="<b>dues</b>",
        amount=Decimal("10.00"),
        currency="THB",
        method="CASH",
        business_date=datetime(2026, 10, 18, tzinfo=UTC).date(),
        issuer_name="Ada",
        status="ISSUED",
    )
    html = render_receipt_html(receipt)
    assert "Internal receipt — not a tax invoice" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;dues&lt;/b&gt;" in html
    assert "https://" not in html
    response = receipt_download_response(receipt)
    assert response["Content-Type"].startswith("text/html")
    assert "charset=utf-8" in response["Content-Type"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert response["Content-Disposition"].startswith("attachment;")

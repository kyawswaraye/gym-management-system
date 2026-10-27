from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from apps.members.payment_serializers import ReceiptSerializer
from apps.members.payment_views import (
    ReceiptDownloadView,
    ReceiptPrintContextView,
    ReceiptPrintView,
)
from apps.members.receipt_print import (
    logo_document,
    print_context_payload,
    receipt_print_payload,
)
from apps.members.receipt_rendering import receipt_download_response


def _receipt() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        payment_id=uuid4(),
        receipt_number="RCPT-2026-000001",
        issued_at=datetime(2026, 10, 27, 4, 0, tzinfo=UTC),
        gym_code="PRN",
        gym_name="Snapshot <Gym>",
        gym_address="Old Street",
        member_number="Walk-in",
        member_name="Walk-in",
        description="Walk-in fee",
        amount=Decimal("15.00"),
        currency="THB",
        method="BANK_TRANSFER",
        business_date=datetime(2026, 10, 27, tzinfo=UTC).date(),
        issuer_name="Mina Manager",
        status="ISSUED",
        created_at=datetime(2026, 10, 27, 4, 1, tzinfo=UTC),
    )


def _settings(*, logo: bool, footer: str = "Thanks") -> SimpleNamespace:
    return SimpleNamespace(
        printer_enabled=True,
        printer_print_logo=logo,
        printer_print_address=False,
        printer_print_phone=True,
        printer_footer=footer,
    )


def test_print_reads_require_receipts_view_and_leave_download_sandboxed() -> None:
    assert ReceiptPrintContextView.required_permission == "receipts.view"
    assert ReceiptPrintView.required_permission == "receipts.view"
    assert ReceiptDownloadView.required_permission == "receipts.view"
    response = receipt_download_response(_receipt())
    assert (
        response["Content-Security-Policy"]
        == "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'"
    )
    assert "Snapshot &lt;Gym&gt;" in response.content.decode()


def test_print_payload_keeps_snapshot_and_honors_logo_toggle() -> None:
    receipt = _receipt()
    gym = SimpleNamespace(phone="092222222", name="Renamed Gym", address_line1="New Street")
    logo = SimpleNamespace(content_type="image/png", data=b"\x89PNG\r\n\x1a\nlogo")
    hidden = receipt_print_payload(
        receipt=receipt,
        gym=gym,
        settings=_settings(logo=False, footer="See you\n<script>"),
        logo=logo,
    )
    shown = receipt_print_payload(
        receipt=receipt,
        gym=gym,
        settings=_settings(logo=True, footer="See you\n<script>"),
        logo=logo,
    )

    assert set(hidden) == set(ReceiptSerializer().fields) | {
        "phone",
        "logo",
        "printer_enabled",
        "printer_print_logo",
        "printer_print_address",
        "printer_print_phone",
        "printer_footer",
    }
    assert hidden["issuer_name"] == "Mina Manager"
    assert hidden["gym_name"] == "Snapshot <Gym>"
    assert hidden["gym_address"] == "Old Street"
    assert hidden["phone"] == "092222222"
    assert hidden["amount"] == "15.00"
    assert hidden["printer_footer"] == "See you\n<script>"
    assert hidden["printer_print_logo"] is False
    assert hidden["logo"] is None
    assert shown["logo"] == logo_document(logo)
    assert shown["logo"]["content_type"] == "image/png"
    assert "<script>" not in shown["logo"]["data_base64"]


def test_print_context_is_current_branding_only() -> None:
    gym = SimpleNamespace(phone="091111111")
    logo = SimpleNamespace(content_type="text/html", data=b"<script>")
    payload = print_context_payload(
        gym=gym,
        settings=_settings(logo=True),
        logo=logo,
    )
    assert set(payload) == {
        "printer_enabled",
        "printer_print_logo",
        "printer_print_address",
        "printer_print_phone",
        "printer_footer",
        "phone",
        "logo",
    }
    assert payload["printer_enabled"] is True
    assert payload["phone"] == "091111111"
    assert payload["logo"] is None
    assert "next_member_number" not in payload


def test_logo_document_drops_empty_or_unexpected_images() -> None:
    assert logo_document(None) is None
    assert logo_document(SimpleNamespace(content_type="image/svg+xml", data=b"<svg>")) is None
    assert logo_document(SimpleNamespace(content_type="image/png", data=b"")) is None
    hostile = SimpleNamespace(content_type="image/png", data=b"<script>alert(1)</script>")
    assert logo_document(hostile) is None
    oversized = b"\x89PNG\r\n\x1a\n" + (b"x" * 1_048_576)
    assert logo_document(SimpleNamespace(content_type="image/png", data=oversized)) is None

"""Read-only printer options and the final receipt print payload.

The receipt row is not updated. Gym name and address stay on the receipt
snapshot. Phone, logo, footer, and printer toggles are the gym's current values.
"""

from __future__ import annotations

import base64
from typing import cast
from uuid import UUID

from django.http import Http404

from apps.members.models import Receipt
from apps.members.payment_serializers import (
    ReceiptPrintContextSerializer,
    ReceiptPrintSerializer,
)
from apps.tenants.logo import LOGO_CONTENT_TYPES, logo_bytes_are_safe
from apps.tenants.models import Gym, GymLogo, GymSettings, StaffProfile


def get_printer_settings(*, gym_id: UUID) -> GymSettings:
    try:
        return GymSettings.objects.get(gym_id=gym_id)
    except GymSettings.DoesNotExist as exc:
        raise Http404 from exc


def printer_logo(*, gym_id: UUID, enabled: bool) -> GymLogo | None:
    if not enabled:
        return None
    logo = GymLogo.objects.filter(pk=gym_id).first()
    if logo is None or logo.content_type not in LOGO_CONTENT_TYPES:
        return None
    return logo


def logo_document(logo: GymLogo | None) -> dict[str, str] | None:
    if logo is None:
        return None
    payload = bytes(logo.data)
    content_type = str(logo.content_type)
    if not logo_bytes_are_safe(content_type, payload):
        return None
    return {
        "content_type": content_type,
        "data_base64": base64.b64encode(payload).decode("ascii"),
    }


def _branding(*, gym: Gym, settings: GymSettings, logo: GymLogo | None) -> dict[str, object]:
    return {
        "phone": gym.phone,
        "logo": logo_document(logo) if settings.printer_print_logo else None,
        "printer_enabled": settings.printer_enabled,
        "printer_print_logo": settings.printer_print_logo,
        "printer_print_address": settings.printer_print_address,
        "printer_print_phone": settings.printer_print_phone,
        "printer_footer": settings.printer_footer,
    }


def print_context_payload(
    *, gym: Gym, settings: GymSettings, logo: GymLogo | None
) -> dict[str, object]:
    return cast(
        dict[str, object],
        ReceiptPrintContextSerializer(_branding(gym=gym, settings=settings, logo=logo)).data,
    )


class _ReceiptPrintSource:
    def __init__(self, receipt: Receipt, branding: dict[str, object]) -> None:
        self._receipt = receipt
        self._branding = branding

    def __getattr__(self, name: str) -> object:
        if name in self._branding:
            return self._branding[name]
        return getattr(self._receipt, name)


def printed_issuer(receipt: Receipt) -> str:
    """The slip shows the staff code. A saved personal name is not printed."""
    stored = str(getattr(receipt, "issuer_name", "") or "")
    gym_id = getattr(receipt, "gym_id", None)
    membership_id = getattr(receipt, "created_by_membership_id", None)
    if gym_id is None or membership_id is None:
        return stored
    code = (
        StaffProfile.objects.filter(gym_id=gym_id, gym_membership_id=membership_id)
        .values_list("staff_code", flat=True)
        .first()
    )
    return str(code or "").strip()


def receipt_print_payload(
    *,
    receipt: Receipt,
    gym: Gym,
    settings: GymSettings,
    logo: GymLogo | None,
) -> dict[str, object]:
    branding = _branding(gym=gym, settings=settings, logo=logo)
    branding["issuer_name"] = printed_issuer(receipt)
    return cast(
        dict[str, object],
        ReceiptPrintSerializer(_ReceiptPrintSource(receipt, branding)).data,
    )

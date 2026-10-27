from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Receipt
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymLogo, GymMembership, GymSettings
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_PNG = b"\x89PNG\r\n\x1a\nlogo"


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


@override_settings(**jwt_settings())
def test_printer_settings_and_receipt_print_payload_are_tenant_safe() -> None:
    client, token = platform_token("printer-platform@example.test")
    gym_a_payload = provision(
        client, token, code="PRN-A", owner_email="printer-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, token, code="PRN-B", owner_email="printer-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    receptionist = add_staff(gym_a, email="printer-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym_a, email="printer-manager@example.test", role_key="MANAGER")
    trainer = add_staff(gym_a, email="printer-trainer@example.test", role_key="TRAINER")
    api = _client()
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    trainer_headers = auth_headers(staff_token(trainer))

    anonymous = APIClient().get("/api/v1/receipts/print-context/")
    assert anonymous.status_code == 403
    assert api.get("/api/v1/settings/", **reception_headers).status_code == 403
    assert api.get("/api/v1/receipts/print-context/", **trainer_headers).status_code == 403

    defaults = api.get("/api/v1/receipts/print-context/", **reception_headers)
    assert defaults.status_code == 200, defaults.json()
    assert defaults.json()["data"] == {
        "printer_enabled": False,
        "printer_print_logo": False,
        "printer_print_address": False,
        "printer_print_phone": False,
        "printer_footer": "",
        "phone": "",
        "logo": None,
    }
    settings_read = api.get("/api/v1/settings/", **owner_headers)
    assert settings_read.status_code == 200, settings_read.json()
    assert settings_read.json()["data"]["printer_enabled"] is False
    assert settings_read.json()["data"]["printer_footer"] == ""

    too_long = api.patch(
        "/api/v1/settings/",
        {"printer_footer": "က" * 121},
        format="json",
        **owner_headers,
    )
    assert too_long.status_code == 400
    assert "printer_footer" in too_long.json()["error"]["fields"]
    four_lines = api.patch(
        "/api/v1/settings/",
        {"printer_footer": "a\nb\nc\nd"},
        format="json",
        **owner_headers,
    )
    assert four_lines.status_code == 400
    assert "printer_footer" in four_lines.json()["error"]["fields"]
    assert (
        api.patch(
            "/api/v1/settings/",
            {"printer_enabled": True},
            format="json",
            **reception_headers,
        ).status_code
        == 403
    )

    profile = api.patch(
        "/api/v1/gym/",
        {
            "name": "PRN-A <Gym>",
            "address_line1": "Old Street",
            "phone": "091111111",
        },
        format="json",
        **owner_headers,
    )
    assert profile.status_code == 200, profile.json()
    saved = api.patch(
        "/api/v1/settings/",
        {
            "printer_enabled": False,
            "printer_print_logo": False,
            "printer_print_address": True,
            "printer_print_phone": True,
            "printer_footer": "Old footer\r\nSecond",
        },
        format="json",
        **owner_headers,
    )
    assert saved.status_code == 200, saved.json()
    assert saved.json()["data"]["printer_footer"] == "Old footer\nSecond"
    assert GymSettings.objects.get(gym=gym_b).printer_footer == ""

    created = api.post(
        "/api/v1/payments/",
        {
            "purpose": "OTHER",
            "description": "Walk-in fee",
            "amount": "15.00",
            "currency": gym_a.currency,
            "method": "BANK_TRANSFER",
            "business_date": gym_today(gym_a).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"prn-create-{uuid4().hex}",
        **reception_headers,
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    submitted = api.post(
        f"/api/v1/payments/{payment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"prn-submit-{uuid4().hex}",
        **reception_headers,
    )
    assert submitted.status_code == 200, submitted.content
    approved = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"prn-approve-{uuid4().hex}",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    receipt_id = approved.json()["data"]["receipt"]["id"]
    stored = Receipt.objects.get(id=receipt_id)
    issued_at = stored.updated_at
    assert stored.gym_name == "PRN-A <Gym>"
    assert stored.gym_address.startswith("Old Street")

    renamed = api.patch(
        "/api/v1/gym/",
        {"name": "PRN-A Renamed", "address_line1": "New Street", "phone": "092222222"},
        format="json",
        **owner_headers,
    )
    assert renamed.status_code == 200, renamed.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymLogo.objects.create(gym=gym_a, content_type="image/png", data=_PNG)
    current = api.patch(
        "/api/v1/settings/",
        {
            "printer_enabled": True,
            "printer_print_logo": True,
            "printer_print_address": False,
            "printer_print_phone": True,
            "printer_footer": "Thanks\n<script>\nCome again",
        },
        format="json",
        **owner_headers,
    )
    assert current.status_code == 200, current.json()

    printed = api.get(f"/api/v1/receipts/{receipt_id}/print/", **reception_headers)
    assert printed.status_code == 200, printed.json()
    body = printed.json()["data"]
    assert body["gym_name"] == "PRN-A <Gym>"
    assert body["gym_address"].startswith("Old Street")
    assert "New Street" not in body["gym_address"]
    assert body["phone"] == "092222222"
    assert body["printer_footer"] == "Thanks\n<script>\nCome again"
    assert body["printer_enabled"] is True
    assert body["printer_print_logo"] is True
    assert body["printer_print_address"] is False
    assert body["logo"]["content_type"] == "image/png"
    assert body["logo"]["data_base64"] == base64.b64encode(_PNG).decode("ascii")
    assert body["receipt_number"]
    assert body["amount"] == "15.00"
    assert body["status"] == "ISSUED"

    hidden = api.patch(
        "/api/v1/settings/",
        {"printer_print_logo": False},
        format="json",
        **owner_headers,
    )
    assert hidden.status_code == 200, hidden.json()
    without_logo = api.get(f"/api/v1/receipts/{receipt_id}/print/", **reception_headers)
    assert without_logo.status_code == 200, without_logo.json()
    assert without_logo.json()["data"]["logo"] is None
    assert without_logo.json()["data"]["printer_print_logo"] is False
    assert base64.b64encode(_PNG).decode("ascii") not in without_logo.content.decode()

    context = api.get("/api/v1/receipts/print-context/", **reception_headers)
    assert context.status_code == 200, context.json()
    assert context.json()["data"]["printer_enabled"] is True
    assert context.json()["data"]["phone"] == "092222222"
    assert context.json()["data"]["logo"] is None
    assert "next_member_number" not in context.json()["data"]

    other_print = api.get(f"/api/v1/receipts/{receipt_id}/print/", **other_headers)
    assert other_print.status_code == 404
    other_context = api.get("/api/v1/receipts/print-context/", **other_headers)
    assert other_context.status_code == 200, other_context.json()
    assert other_context.json()["data"]["printer_footer"] == ""
    assert other_context.json()["data"]["phone"] == ""

    download = api.get(f"/api/v1/receipts/{receipt_id}/download/", **reception_headers)
    assert download.status_code == 200
    assert (
        download["Content-Security-Policy"]
        == "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'"
    )
    assert "PRN-A &lt;Gym&gt;" in download.content.decode()
    assert "<script>" not in download.content.decode()

    stored.refresh_from_db()
    assert stored.updated_at == issued_at
    assert stored.gym_name == "PRN-A <Gym>"
    assert stored.status == "ISSUED"
    assert GymSettings.objects.get(gym=gym_b).printer_enabled is False

    settings_row = GymSettings.objects.get(gym=gym_a)
    settings_row.printer_footer = "a\nb\nc\nd"
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            settings_row.save(update_fields=["printer_footer", "updated_at"])
    assert GymSettings.objects.get(gym=gym_a).printer_footer == "Thanks\n<script>\nCome again"

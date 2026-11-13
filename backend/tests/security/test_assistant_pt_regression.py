"""PT, payment, and confirmation behavior after the token-budget changes.

The scripted provider chooses the tools. It proves what the server does with
those tools. It does not prove that live Gemini selects them.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.assistant.models import Goal
from apps.assistant.provider import FunctionCall, ModelTurn
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Payment
from apps.members.services import create_member
from apps.tenants.models import Gym, GymSettings
from apps.trainers.models import PTBooking, PTSale, ServicePackage, TrainerAssignment
from apps.trainers.services import create_service_package
from tests.security.test_assistant_member_reads import _owner
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings, staff_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_FIXED = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
_NO_RECORD = "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။"


class _Clock:
    def __call__(self) -> datetime:
        return _FIXED


def _settings() -> dict[str, str]:
    return {
        **jwt_settings(),
        "ASSISTANT_ENABLED": "true",
        "GEMINI_API_KEY": "k" * 20,
        "GEMINI_MODEL": "gemini-test",
        "ASSISTANT_MAX_STEPS": "4",
    }


class _Script:
    """Return queued model turns and record declaration shape, not prompt text."""

    def __init__(self) -> None:
        self.queue: list[ModelTurn] = []
        self.traces: list[dict[str, object]] = []

    def push_call(self, name: str, arguments: dict[str, object], text: str = "") -> None:
        parts: list[dict[str, object]] = []
        if text:
            parts.append({"text": text})
        parts.append({"functionCall": {"name": name, "args": arguments}})
        self.queue.append(ModelTurn(text, (FunctionCall(name, arguments),), tuple(parts)))

    def push_text(self, text: str) -> None:
        self.queue.append(ModelTurn(text, (), ({"text": text},)))

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del contents, transport
        names = {
            item["name"]
            for item in declarations
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if "Relevant operation. Personal training" in system_instruction:
            capability = "pt"
        elif "Relevant operation. Members" in system_instruction:
            capability = "members"
        elif "Relevant operation. Payments" in system_instruction:
            capability = "payments"
        else:
            capability = "other"
        self.traces.append(
            {
                "capability": capability,
                "create_assignment": "create_assignment" in names,
                "create_pt_sale": "create_pt_sale" in names,
                "create_pt_booking": "create_pt_booking" in names,
                "search_payments": "search_payments" in names,
                "write_count": sum(
                    1 for name in names if name.startswith("create_") or name.startswith("update_")
                ),
            }
        )
        if not self.queue:
            return ModelTurn("ရပါပြီ။", (), ({"text": "ရပါပြီ။"},))
        return self.queue.pop(0)


def _world(monkeypatch: pytest.MonkeyPatch, code: str) -> tuple[Gym, object, Member, str]:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner(code, f"{code.lower()}-owner@example.test")
    gym.timezone = "Asia/Yangon"
    gym.save(update_fields=["timezone"])
    for feature in ("members", "memberships", "finance", "reports", "pt"):
        _enable(gym, feature)
    trainer_membership = add_staff(
        gym, email=f"{code.lower()}-soe@example.test", role_key="TRAINER"
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        settings = GymSettings.objects.select_for_update().get(gym=gym)
        settings.next_member_number = 5
        settings.save(update_fields=["next_member_number", "updated_at"])
        member = create_member(
            gym=gym,
            actor=owner,
            member_number="",
            first_name="ဝင့်",
            last_name="ဝါ",
            phone=None,
            email=None,
            gender=None,
            date_of_birth=None,
            address={},
            join_date=date(2026, 11, 1),
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
        )
        _profile(gym, trainer_membership, code=f"{code}SOE", name="Soe Soe")
        create_service_package(
            gym=gym,
            actor=owner,
            package_type=ServicePackage.PackageType.PT,
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id=f"req_{code.lower()}_pkg",
            ip_address=None,
            user_agent="",
        )
    assert member.member_number == "MEM-000005"
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    return gym, client, member, headers  # type: ignore[return-value]


def _post(
    client: APIClient, headers: dict[str, str], message: str, conversation_id: str = ""
) -> dict[str, object]:
    payload: dict[str, object] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/assistant/turns/", payload, format="json", **headers)
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _confirm(
    client: APIClient, headers: dict[str, str], step_id: str
) -> tuple[int, dict[str, object]]:
    response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    body = response.json()
    data = body.get("data")
    return response.status_code, data if isinstance(data, dict) else {"error": body}


def _reject(
    client: APIClient, headers: dict[str, str], step_id: str
) -> tuple[int, dict[str, object]]:
    response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/reject/",
        {},
        format="json",
        **headers,
    )
    body = response.json()
    data = body.get("data")
    return response.status_code, data if isinstance(data, dict) else {"error": body}


def _reload(client: APIClient, headers: dict[str, str], conversation_id: str) -> dict[str, object]:
    response = client.get(f"/api/v1/assistant/conversations/{conversation_id}/", **headers)
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


@override_settings(**_settings())
def test_scripted_pt_payment_and_confirmation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="apps.assistant.turns")
    gym, client, member, headers = _world(monkeypatch, "RG01")
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    script.push_call("search_members", {"search": "ဝင့်ဝါ"})
    script.push_text("သင်တန်းဆရာ Soe Soe ရှိပါတယ်။ ဒီသင်တန်းဆရာကို သုံးမလား။")
    opened = _post(client, headers, "ဝင့်ဝါကို PT ထည့်မယ်။")
    conversation = str(opened["conversation_id"])

    script.push_text("ဘယ်ရက်က စမလဲ။")
    _post(client, headers, "Soe Soe", conversation)

    script.push_call(
        "create_assignment",
        {
            "member_id": "ဝင့် ဝါ",
            "trainer_staff_profile_id": "Soe Soe",
            "effective_from": "2026-01-01",
        },
        text="အတည်ပြုပါ။",
    )
    proposed = _post(client, headers, "ဒီနေ့", conversation)
    pending = proposed["pending_confirmation"]
    date_trace = script.traces[-1] if script.traces else {}
    problems: list[str] = []
    body = str(proposed["message"]["body"])
    if body == _NO_RECORD:
        problems.append("date turn returned the generic missing-record sentence")
    if not isinstance(pending, dict) or "Assign a trainer" not in str(pending.get("summary")):
        problems.append(f"date turn did not propose an assignment: {pending!r} body={body}")
    fields = pending.get("fields") if isinstance(pending, dict) else None
    if not isinstance(fields, list) or not any(
        isinstance(field, dict) and "2026-11-12" in str(field.get("value")) for field in fields
    ):
        problems.append(f"confirmation fields did not include the start date: {fields!r}")
    if date_trace.get("create_assignment") is not True:
        problems.append("date turn hid create_assignment")

    assignment_id = ""
    if isinstance(pending, dict):
        assignment_id = str(pending["step_id"])
        reloaded = _reload(client, headers, conversation)
        if reloaded.get("pending_confirmation") is None:
            problems.append("reopen dropped the pending assignment")
        status, saved = _confirm(client, headers, assignment_id)
        if status != 200 or saved.get("outcome") not in {"committed", "existing"}:
            problems.append(f"confirm failed: {status} {saved.get('outcome')}")
        status_again, again = _confirm(client, headers, assignment_id)
        if status_again != 200 or again.get("outcome") != "existing":
            problems.append(
                f"second confirm was not idempotent: {status_again} {again.get('outcome')}"
            )
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            rows = list(TrainerAssignment.objects.filter(gym=gym, member=member))
        if len(rows) != 1 or rows[0].effective_from != date(2026, 11, 12):
            problems.append(
                "assignment rows=" + ",".join(row.effective_from.isoformat() for row in rows)
            )
        followed = str(
            saved.get("message", {}).get("body") if isinstance(saved.get("message"), dict) else ""
        )
        if "ဘိုကင်" in followed and "ပက်ကေ့" not in followed:
            problems.append("follow-up offered a booking before a package")

        script.push_call(
            "create_pt_sale",
            {
                "member_id": "ဝင့် ဝါ",
                "trainer_staff_profile_id": "Soe Soe",
                "service_package_id": "PT3",
                "service_date": "2026-01-01",
                "description": "Private sessions",
                "gross_fee": "100.00",
                "currency": gym.currency,
            },
            text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
        )
        sale_turn = _post(client, headers, "PT3 ပက်ကေ့ ရောင်းပေး", conversation)
        sale_pending = sale_turn["pending_confirmation"]
        if (
            not isinstance(sale_pending, dict)
            or "sale" not in str(sale_pending.get("summary")).casefold()
        ):
            problems.append(
                f"package sale was not proposed: {sale_pending!r} {sale_turn['message']}"
            )
        else:
            reject_status, rejected = _reject(client, headers, str(sale_pending["step_id"]))
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                rejected_sales = PTSale.objects.filter(gym=gym).count()
            if (
                reject_status != 200
                or rejected.get("outcome") != "cancelled"
                or rejected_sales != 0
            ):
                problems.append(
                    f"reject did not cancel the sale: {reject_status} {rejected.get('outcome')}"
                )
            gone = _reload(client, headers, conversation)
            if gone.get("pending_confirmation") is not None:
                problems.append("rejected sale stayed pending")
            script.push_call(
                "create_pt_sale",
                {
                    "member_id": "ဝင့် ဝါ",
                    "trainer_staff_profile_id": "Soe Soe",
                    "service_package_id": "PT3",
                    "service_date": "2026-01-01",
                    "description": "Private sessions",
                    "gross_fee": "100.00",
                    "currency": gym.currency,
                },
                text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
            )
            again = _post(client, headers, "PT3 ပက်ကေ့ ရောင်းပေး", conversation)
            again_pending = again["pending_confirmation"]
            if not isinstance(again_pending, dict):
                problems.append(f"package sale was not proposed again: {again['message']}")
            else:
                sale_status, _sold = _confirm(client, headers, str(again_pending["step_id"]))
                if sale_status != 200:
                    problems.append(f"package sale confirm status {sale_status}")
                sale_again_status, sale_again = _confirm(
                    client, headers, str(again_pending["step_id"])
                )
                if sale_again_status != 200 or sale_again.get("outcome") != "existing":
                    problems.append(
                        f"second sale confirm was not idempotent: {sale_again.get('outcome')}"
                    )
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            if PTSale.objects.filter(gym=gym).count() != 1:
                problems.append("pt sale count is not 1")
            if PTBooking.objects.filter(gym=gym).count() != 0:
                problems.append("a booking was written during the package sale")

    script.push_call("search_members", {"search": "ဝင့်ဝါ"})
    script.push_call("search_payments", {"member": str(member.id)})
    script.push_text("ဒီအဖွဲ့ဝင်အတွက် ငွေပေးချေမှု မှတ်တမ်း မရှိပါ။")
    payment = _post(client, headers, "ဝင့်ဝါ ငွေပေးချေမှု ရှာ")
    payment_body = str(payment["message"]["body"])
    if payment_body == _NO_RECORD or "ငွေပေးချေမှု" not in payment_body:
        problems.append(f"payment lookup body={payment_body}")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if Payment.objects.filter(gym=gym).count() != 0:
            problems.append("payment lookup wrote a payment")

    script.push_call(
        "create_assignment",
        {
            "member_id": "ဝင့် ဝါ",
            "trainer_staff_profile_id": "Soe Soe",
            "effective_from": "2026-11-12",
        },
        text="အတည်ပြုပါ။",
    )
    duplicate = _post(client, headers, "ဝင့်ဝါကို Soe Soe PT ထည့်မယ်။ ဒီနေ့။")
    duplicate_body = str(duplicate["message"]["body"])
    if duplicate.get("pending_confirmation") is not None or "ရှိပြီးသား" not in duplicate_body:
        problems.append(
            f"duplicate assignment was not reported as already stored: {duplicate['message']}"
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if TrainerAssignment.objects.filter(gym=gym).count() != 1:
            problems.append("duplicate assignment wrote another row")

    if assignment_id:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            goal = Goal.objects.filter(gym=gym, status=Goal.Status.COMPLETED).first()
        if goal is not None:
            goal.expires_at = _FIXED - timedelta(days=2)
            goal.status = Goal.Status.WAITING_CONFIRMATION
            goal.is_active = True
            goal.save(update_fields=["expires_at", "status", "is_active", "updated_at"])
            step = goal.steps.filter(tool_name="create_assignment").first()
            if step is not None:
                step.status = "waiting_confirmation"
                step.save(update_fields=["status", "updated_at"])
                expired_status, _expired = _confirm(client, headers, str(step.id))
                if expired_status == 200:
                    problems.append("expired confirmation executed")

    turn_logs = [
        record.message for record in caplog.records if record.message.startswith("assistant turn ")
    ]
    assert not problems, "\n".join(
        [*problems, *turn_logs, *[str(trace) for trace in script.traces]]
    )


@override_settings(**_settings())
def test_live_gemini_pt_and_payment(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    if os.environ.get("ASSISTANT_LIVE_GEMINI") != "1":
        pytest.skip("Set ASSISTANT_LIVE_GEMINI=1 to call the configured Gemini model")
    key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "")
    if len(key) < 20 or not model:
        pytest.skip("NOT VERIFIED: GEMINI_API_KEY or GEMINI_MODEL is missing")
    caplog.set_level(logging.INFO, logger="apps.assistant.turns")
    gym, client, member, headers = _world(monkeypatch, "RG02")
    with override_settings(GEMINI_API_KEY=key, GEMINI_MODEL=model, ASSISTANT_ENABLED="true"):
        first = _post(client, headers, "ဝင့်ဝါကို PT ထည့်မယ်။")
        conversation = str(first["conversation_id"])
        second = _post(client, headers, "Soe Soe", conversation)
        third = _post(client, headers, "ဒီနေ့", conversation)
        payment = _post(client, headers, "MEM-000005 ငွေပေးချေမှု ရှာ")
    pending = third.get("pending_confirmation")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assignments = TrainerAssignment.objects.filter(gym=gym, member=member).count()
    logs = [
        record.message for record in caplog.records if record.message.startswith("assistant turn ")
    ]
    report = {
        "first": str(first["message"]["body"])[:240],
        "second": str(second["message"]["body"])[:240],
        "third": str(third["message"]["body"])[:240],
        "third_pending": isinstance(pending, dict),
        "payment": str(payment["message"]["body"])[:240],
        "assignments_before_confirm": assignments,
        "logs": logs,
    }
    assert report["third_pending"], report["third"] + " | " + " || ".join(logs)
    assert report["payment"] != _NO_RECORD, report
    assert "ငွေပေးချေမှု" in report["payment"], report
    assert all("limit_reached=False" in line for line in logs), report

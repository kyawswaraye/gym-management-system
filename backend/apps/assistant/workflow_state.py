"""Persisted workflow facts for one staff conversation.

The row is a system message on the conversation. It is not sent back to the
model as chat text. Tool results update it. A later provider failure does not
erase a member, plan, or date the database already returned.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from django.db import transaction

from apps.assistant.knowledge import shown_day, speaks_burmese
from apps.assistant.models import Conversation, Message
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership
from apps.tenants.models import Gym, GymSettings

_MARKER = "workflow-state v1\n"
_KEYS = (
    "workflow",
    "active_workflows",
    "goals",
    "stage",
    "member_id",
    "member_number",
    "member_name",
    "plan_id",
    "plan_code",
    "plan_name",
    "package_id",
    "package_code",
    "package_name",
    "start_date",
    "membership_id",
    "payment_status",
    "last_tool",
    "last_outcome",
    "last_reference",
    "next",
    "pending_tool",
    "parked",
    "payment_id",
    "product_id",
    "trainer_id",
    "pt_sale_id",
    "pt_booking_id",
    "occurrence_id",
    "class_booking_id",
    "salary_period_id",
    "retail_sale_id",
    "expense_id",
    "announcement_id",
    "record_status",
    "return_key",
    "saved_changes",
    "verified",
)


def load_state(gym: Gym, conversation: Conversation) -> dict[str, str]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = (
            Message.objects.filter(
                gym=gym,
                conversation=conversation,
                role=Message.Role.SYSTEM,
                body__startswith=_MARKER,
            )
            .order_by("created_at", "id")
            .only("body")
            .first()
        )
    if row is None:
        return {}
    try:
        parsed = json.loads(row.body[len(_MARKER) :])
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {key: str(parsed[key]) for key in _KEYS if isinstance(parsed.get(key), str)}


def save_state(gym: Gym, conversation: Conversation, state: Mapping[str, str]) -> None:
    public = _public_state(state)
    body = _MARKER + json.dumps(public, ensure_ascii=False, separators=(",", ":"))
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = (
            Message.objects.filter(
                gym=gym,
                conversation=conversation,
                role=Message.Role.SYSTEM,
                body__startswith=_MARKER,
            )
            .order_by("created_at", "id")
            .first()
        )
        if row is None:
            if not any(public.values()):
                return
            Message.objects.create(
                gym=gym,
                conversation=conversation,
                role=Message.Role.SYSTEM,
                body=body,
            )
            return
        if row.body != body:
            row.body = body
            row.save(update_fields=["body", "updated_at"])


def is_state_message(message: Message) -> bool:
    return message.role == Message.Role.SYSTEM and message.body.startswith(_MARKER)


_SINGLE_IDS = {
    "search_payments": "payment_id",
    "search_products": "product_id",
    "search_trainers": "trainer_id",
    "search_pt_sales": "pt_sale_id",
    "search_pt_bookings": "pt_booking_id",
    "search_class_occurrences": "occurrence_id",
    "search_bookings": "class_booking_id",
}


def absorb_read(state: dict[str, str], tool: str, result: Mapping[str, object]) -> None:
    if tool == "get_payment":
        payment_id = _uuid(result.get("record_id"))
        if payment_id:
            state["payment_id"] = payment_id
        member_id = _uuid(result.get("member_id"))
        if member_id and not _open_registration(state):
            state["member_id"] = member_id
        return
    rows = result.get("results")
    if not isinstance(rows, list):
        return
    mapped = [row for row in rows if isinstance(row, Mapping)]
    if tool == "search_members" and len(mapped) == 1 and not result.get("ambiguous"):
        if not _open_registration(state):
            _remember_member_row(state, mapped[0])
    elif tool == "get_member" and mapped:
        if not _open_registration(state):
            _remember_member_row(state, mapped[0])
    elif tool == "search_membership_plans" and len(mapped) == 1 and not result.get("ambiguous"):
        row = mapped[0]
        if str(row.get("status") or "") == "ACTIVE" and _plan_matches(state, row):
            _remember_plan_row(state, row)
    elif tool == "search_memberships" and len(mapped) == 1:
        row = mapped[0]
        state["membership_id"] = _text(row.get("id")) or state.get("membership_id", "")
        state["payment_status"] = _text(row.get("payment_status")) or state.get(
            "payment_status", ""
        )
        found_start = _text(row.get("start_date"))
        if found_start and not _kept_start(state):
            state["start_date"] = found_start
        if state.get("workflow") == "":
            state["workflow"] = "membership"
    elif tool == "search_service_packages" and len(mapped) == 1 and not result.get("ambiguous"):
        _remember_package_row(state, mapped[0])
    elif tool in _SINGLE_IDS and len(mapped) == 1 and not result.get("ambiguous"):
        found = _uuid(mapped[0].get("id"))
        if found:
            state[_SINGLE_IDS[tool]] = found


def _remember_package_row(state: dict[str, str], row: Mapping[str, Any]) -> None:
    found = _text(row.get("code"))
    stored = state.get("package_code") or ""
    if found and (not stored or stored.casefold() == found.casefold()):
        state["package_code"] = found
        name = _text(row.get("name"))
        if name:
            state["package_name"] = name


def absorb_proposal(
    state: dict[str, str],
    tool: str,
    payload: Mapping[str, object],
) -> None:
    state["stage"] = "waiting_confirmation"
    state["pending_tool"] = tool
    state["last_tool"] = tool
    state["last_outcome"] = "waiting_confirmation"
    _copy_payload(state, payload)
    state["next"] = ""


def _stored_status(gym: Gym, tool: str) -> str:
    """Return the status a saved finance or announcement step actually leaves."""

    if tool.startswith("approve_"):
        return "POSTED"
    if tool == "publish_announcement":
        return "PUBLISHED"
    if tool in _DRAFTS:
        return "DRAFT"
    if tool not in _SUBMITTED:
        return ""
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        settings = GymSettings.objects.filter(gym=gym).first()
    separate = settings is None or settings.require_separate_finance_approval
    return "PENDING_APPROVAL" if separate else "POSTED"


def return_fingerprint(payload: Mapping[str, object]) -> str:
    """Identify one return so the same goods are not returned again."""

    sale = payload.get("retail_sale_id") or payload.get("sale_route_id") or ""
    lines = payload.get("lines")
    parts: list[str] = []
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict):
                continue
            parts.append(f"{line.get('line_id')}:{line.get('quantity')}")
    return f"{sale}|{','.join(sorted(parts))}"


def remember_commit(
    gym: Gym,
    state: dict[str, str],
    tool: str,
    payload: Mapping[str, object],
    reference: str | None,
) -> None:
    state["stage"] = "completed"
    state["pending_tool"] = ""
    state["last_tool"] = tool
    state["last_outcome"] = "saved"
    state["last_reference"] = reference or ""
    _copy_payload(state, payload)
    status = _stored_status(gym, tool)
    if status:
        state["record_status"] = status
    if tool == "return_retail_sale":
        state["return_key"] = return_fingerprint(payload)
    if not reference:
        state["next"] = ""
        return
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if tool == "create_member":
            member = Member.objects.filter(gym=gym, id=reference).first()
            if member is not None:
                state["member_id"] = str(member.id)
                state["member_number"] = member.member_number
                state["member_name"] = _person(member.first_name, member.last_name)
                state["workflow"] = "member"
        elif tool == "create_membership":
            membership = (
                Membership.objects.filter(gym=gym, id=reference).select_related("member").first()
            )
            if membership is not None:
                state["membership_id"] = str(membership.id)
                state["member_id"] = str(membership.member_id)
                state["member_number"] = membership.member.member_number
                state["member_name"] = _person(
                    membership.member.first_name,
                    membership.member.last_name,
                )
                state["plan_code"] = membership.plan_code
                state["plan_name"] = membership.plan_name
                state["start_date"] = membership.start_date.isoformat()
                state["payment_status"] = membership.payment_status
                state["workflow"] = "membership"
    state["next"] = ""


def completion_sentence(state: Mapping[str, str], staff_text: str) -> str:
    return _done_sentence(state, speaks_burmese(staff_text))


def provider_notice(staff_text: str, state: Mapping[str, str]) -> str:
    """Say whether earlier work remains stored. This is not a new success."""

    burmese = speaks_burmese(staff_text)
    if state.get("last_outcome") == "saved":
        kept = _kept_sentence(state, burmese)
        if burmese:
            return f"{kept} အခု ခဏ ဆက်မပြောနိုင်ပါ။"
        return f"{kept} I could not continue just now."
    if state.get("last_outcome") == "waiting_confirmation":
        if burmese:
            return "အတည်ပြုချက် စောင့်နေပါသေးတယ်။ အခု ခဏ ဆက်မပြောနိုင်ပါ။ ဘာမှ အသစ် မသိမ်းရသေးပါ။"
        return (
            "A confirmation is still waiting. I could not continue just now. Nothing new was saved."
        )
    if burmese:
        return "လက်ထောက်ကို အခု ခဏ အသုံးမပြုနိုင်ပါ။ ဘာမှ အသစ် မသိမ်းရသေးပါ။"
    return "The assistant is unavailable. Nothing new was saved."


def _open_registration(state: Mapping[str, str]) -> bool:
    return bool(state.get("member_name")) and not state.get("member_id")


def _plan_matches(state: Mapping[str, str], row: Mapping[str, Any]) -> bool:
    stored = state.get("plan_code") or ""
    found = _text(row.get("code"))
    return not stored or not found or stored.casefold() == found.casefold()


def _kept_start(state: Mapping[str, str]) -> bool:
    return bool(state.get("start_date")) and state.get("workflow") not in {"", "membership"}


_DATE_QUESTIONS = (
    "which date",
    "which start date",
    "what date",
    "what start date",
    "ဘယ်ရက်လဲ",
    "ဘယ်ရက်က စမလဲ",
    "ဘယ်နေ့လဲ",
)


def asks_stored_date(text: str) -> bool:
    """A short question about the date, with no new date in the same message."""

    from apps.assistant.lookups import calendar_day

    found = calendar_day(text, date.today())
    if found is not None:
        return False
    folded = " ".join(text.casefold().split()).strip("။.!?၊ ")
    if len(folded) > 40:
        return False
    return any(phrase in folded or phrase in text for phrase in _DATE_QUESTIONS)


def stored_date_reply(text: str, state: Mapping[str, str]) -> str | None:
    """Repeat a validated date instead of asking for it again."""

    if not asks_stored_date(text):
        return None
    iso = state.get("start_date") or ""
    if len(iso) < 10:
        return None
    detail = ", ".join(
        part
        for part in (
            state.get("member_name") or "",
            state.get("plan_code") or state.get("plan_name") or "",
            shown_day(iso),
        )
        if part
    )
    if speaks_burmese(text):
        return f"စတင်ရက်က {detail} ပါ။"
    return f"The date is {detail}."


def confirmation_reply(
    reply: str,
    proposal: Mapping[str, object] | None,
    staff_text: str,
    state: Mapping[str, str],
) -> str:
    """Keep the collected name, plan, and date on a confirmation that is not saved yet."""

    if proposal is None:
        return reply
    generic = {
        "That change is not saved until you confirm.",
        "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ။ အတည်ပြုပြီးမှ သိမ်းပါမယ်။",
    }
    if reply.strip() not in generic:
        return reply
    facts = _proposal_facts(proposal, state)
    if not facts:
        return reply
    burmese = speaks_burmese(staff_text) or speaks_burmese(reply)
    shown = "၊ ".join(facts) if burmese else ", ".join(facts)
    if burmese:
        return f"{shown}။ {reply.strip()}"
    return f"{shown}. {reply.strip()}"


def _proposal_facts(proposal: Mapping[str, object], state: Mapping[str, str]) -> list[str]:
    fields = _proposal_fields(proposal)
    name = _person(fields.get("first_name", ""), fields.get("last_name", "")) or (
        state.get("member_name") or ""
    )
    plan = fields.get("plan_id", "")
    if _uuid(plan):
        plan = ""
    plan = plan or state.get("plan_code") or state.get("plan_name") or ""
    day = ""
    for key in ("start_date", "join_date", "service_date", "effective_from", "business_date"):
        value = fields.get(key, "")
        if value:
            day = value
            break
    if not day:
        day = state.get("start_date") or ""
    shown = shown_day(day) if day else ""
    return [part for part in (name, plan, shown) if part]


def _proposal_fields(proposal: Mapping[str, object]) -> dict[str, str]:
    listed = proposal.get("fields")
    if not isinstance(listed, list):
        return {}
    found: dict[str, str] = {}
    for field in listed:
        if not isinstance(field, Mapping):
            continue
        name = field.get("name")
        value = field.get("value")
        if isinstance(name, str) and isinstance(value, str) and name and value.strip():
            found[name] = value.strip()
    return found


_BRIEF_SKIP = frozenset({"verified", "next", "goals", "workflow", "parked", "saved_changes"})


def note_saved_change(state: dict[str, str], tool: str, plain: Mapping[str, str]) -> None:
    """Remember one saved write so a later turn does not propose it again."""

    try:
        existing = json.loads(state.get("saved_changes") or "[]")
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append({"tool": tool, "payload": dict(plain)})
    state["saved_changes"] = json.dumps(existing[-6:], ensure_ascii=False)[:4000]


def note_verified(state: dict[str, str], tool: str, body: Mapping[str, object]) -> None:
    """Keep a short verified result for the next turn. This is not chat text."""

    try:
        existing = json.loads(state.get("verified") or "[]")
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append({"tool": tool, "outcome": _compact_body(body)})
    encoded = json.dumps(existing[-6:], default=str, ensure_ascii=False)
    state["verified"] = encoded[:4000]


def _compact_body(body: Mapping[str, object]) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in body.items():
        if key not in {"error", "status", "field", "summary", "result"}:
            continue
        kept[key] = _compact_value(value, 0)
    return kept


def _compact_value(value: object, depth: int) -> object:
    if depth > 2:
        return "..."
    if isinstance(value, Mapping):
        trimmed: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 12:
                break
            trimmed[str(key)[:40]] = _compact_value(item, depth + 1)
        return trimmed
    if isinstance(value, list):
        return [_compact_value(item, depth + 1) for item in value[:5]]
    if isinstance(value, str) and len(value) > 180:
        return value[:180]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)[:180]


def state_brief(state: Mapping[str, str]) -> str:
    parts = [f"{key}={state[key]}" for key in _KEYS if key not in _BRIEF_SKIP and state.get(key)]
    if parts:
        lines = ["Verified references, already stored: " + "; ".join(parts)]
    else:
        lines = ["Verified references: none yet."]
    verified = state.get("verified") or ""
    if verified and verified != "[]":
        lines.append("Recent tool results: " + verified)
    return "\n".join(lines)


def _public_state(state: Mapping[str, str]) -> dict[str, str]:
    return {key: str(state.get(key) or "") for key in _KEYS}


def _remember_member_row(state: dict[str, str], row: Mapping[str, Any]) -> None:
    state["member_id"] = _text(row.get("id")) or state.get("member_id", "")
    state["member_number"] = _text(row.get("member_number")) or state.get("member_number", "")
    state["member_name"] = _person(_text(row.get("first_name")), _text(row.get("last_name"))) or (
        state.get("member_name", "")
    )
    state["workflow"] = state.get("workflow") or "member"


def _remember_plan_row(state: dict[str, str], row: Mapping[str, Any]) -> None:
    state["plan_id"] = _text(row.get("id"))
    state["plan_code"] = _text(row.get("code"))
    state["plan_name"] = _text(row.get("name"))


def _copy_payload(state: dict[str, str], payload: Mapping[str, object]) -> None:
    member_id = _uuid(payload.get("member_id"))
    plan_id = _uuid(payload.get("plan_id"))
    if member_id:
        state["member_id"] = member_id
    if plan_id:
        state["plan_id"] = plan_id
    package_id = _uuid(payload.get("service_package_id"))
    if package_id:
        state["package_id"] = package_id
    start = _text(payload.get("start_date")) or _text(payload.get("join_date"))
    if len(start) >= 10:
        state["start_date"] = start[:10]
    name = _person(_text(payload.get("first_name")), _text(payload.get("last_name")))
    if name:
        state["member_name"] = name
    for key in (
        "salary_period_id",
        "retail_sale_id",
        "expense_id",
        "announcement_id",
        "payment_id",
    ):
        value = _uuid(payload.get(key))
        if value:
            state[key] = value


_DRAFTS = frozenset(
    {
        "create_salary_period",
        "update_salary_period",
        "replace_salary_lines",
        "create_payment",
        "update_payment",
        "create_expense",
        "update_expense",
        "create_retail_sale",
        "update_retail_sale",
        "create_pt_sale",
        "update_pt_sale",
        "create_announcement",
        "update_announcement",
    }
)
_SUBMITTED = frozenset(
    {
        "submit_expense",
        "submit_salary_period",
        "submit_payment",
        "submit_pt_sale",
        "submit_retail_sale",
    }
)


def _done_sentence(state: Mapping[str, str], burmese: bool) -> str:
    saved_submit = state.get("last_outcome") == "saved" and state.get("last_tool") in _SUBMITTED
    if state.get("next") == "approval" or saved_submit:
        if burmese:
            return "တင်ပြီးပါပြီ။ ခွင့်ရှိတဲ့ ဝန်ထမ်းက အတည်ပြုဖို့ ကျန်ပါသေးတယ်။"
        return "It is submitted. An authorized staff member still has to approve it."
    name = state.get("member_name") or ""
    number = state.get("member_number") or ""
    tool = state.get("last_tool") or ""
    if tool == "create_member" and number:
        if burmese:
            return f"{name} ကို သိမ်းပြီးပါပြီ။ အဖွဲ့ဝင်နံပါတ် {number}။"
        return f"{name} is saved. Member number {number}."
    if tool == "create_membership":
        plan = state.get("plan_name") or state.get("plan_code") or ""
        start = state.get("start_date") or ""
        if burmese:
            return f"{name} အတွက် {plan} ကို {start} မှ သိမ်းပြီးပါပြီ။"
        return f"The {plan} membership for {name} starts {start}."
    if state.get("record_status") == "DRAFT" and state.get("last_outcome") != "saved":
        if burmese:
            return "စာရင်းမူကြမ်း သိမ်းပြီးပါပြီ။ မတင်ရသေးပါ။"
        return "The draft is saved. It is not submitted."
    if burmese:
        return "သိမ်းပြီးပါပြီ။"
    return "That change is saved."


def _kept_sentence(state: Mapping[str, str], burmese: bool) -> str:
    name = state.get("member_name") or ""
    number = state.get("member_number") or ""
    if number:
        if burmese:
            return f"{name} အဖွဲ့ဝင်နံပါတ် {number} ရှိနေပါသေးတယ်။".strip()
        return f"{name} member number {number} is still stored.".strip()
    if burmese:
        return "အရင်သိမ်းထားတာက ရှိနေပါသေးတယ်။"
    return "The earlier change is still stored."


def _person(first: str, last: str) -> str:
    return " ".join(part for part in (first.strip(), last.strip()) if part)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return str(UUID(value))
    except ValueError:
        return ""

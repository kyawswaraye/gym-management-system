"""Persisted workflow facts for one staff conversation.

The row is a system message on the conversation. It is not sent back to the
model as chat text. Tool results update it. A later provider failure does not
erase a member, plan, or date the database already returned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction

from apps.assistant.knowledge import (
    information_question,
    short_reply_kind,
    shown_day,
    speaks_burmese,
)
from apps.assistant.models import Conversation, Message
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership
from apps.tenants.models import Gym

_MARKER = "workflow-state v1\n"
_KEYS = (
    "workflow",
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
    "verified",
)
_NEXT_WORDS = (
    "ဘာဆက်",
    "ဘာလုပ်ဖို့",
    "ကျန်သေး",
    "what next",
    "what remains",
    "what should",
)
_CAPABILITIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("member", "အဖွဲ့ဝင်", "membership", "အသင်းဝင်", "plan", "အစီအစဉ်"),
        "Members and memberships. Reads: search_members, get_member, "
        "search_memberships, search_membership_plans. Writes that wait for "
        "confirmation: create_member, update_member, archive_member, "
        "restore_member, create_membership, suspend_membership, "
        "resume_membership, cancel_membership. A new member needs a name and "
        "join date. The server assigns the member number. A new membership "
        "needs that member, an active plan, and a start date. Pending, active, "
        "and suspended ranges cannot overlap. Assigning a membership does not "
        "record a payment. Next, offer an active plan, then a payment if the "
        "membership is unpaid.",
    ),
    (
        ("payment", "receipt", "refund", "ငွေပေး", "ပြန်အမ်း", "ဘောင်ချာ"),
        "Payments. Reads: search_payments, get_payment. A refund is a payment "
        "whose entry_type is REFUND. Writes wait for confirmation: "
        "create_payment, update_payment, submit_payment, cancel_payment, "
        "approve_payment, reject_payment, void_payment, refund_payment, "
        "void_receipt. Cash needs an open cash session. Separate finance "
        "approval stays pending until an authorized role approves it. Do not "
        "call an unpaid membership paid.",
    ),
    (
        ("pt", "trainer", "package", "commission", "ကော်မရှင်", "နည်းပြ"),
        "Personal training. Reads: search_service_packages for catalog prices, "
        "search_pt_packages for a member's remaining sessions, search_pt_commissions. "
        "Writes wait for confirmation: create_assignment, create_pt_sale, "
        "submit_pt_sale, approve_pt_sale, reject_pt_sale, create_pt_booking, "
        "attend_pt_booking, cancel_pt_booking. A sale or booking needs an "
        "active trainer assignment covering the date. A metered booking needs "
        "an active package with sessions left. Next, say the date, trainer, "
        "and remaining sessions after a saved booking.",
    ),
    (
        ("class", "booking", "အတန်း", "ဘိုကင်"),
        "Classes. Reads: search_class_occurrences, search_bookings. Writes "
        "wait for confirmation: create_class_schedule, create_class_booking, "
        "attend_class_booking, cancel_class_booking. A booking needs a "
        "scheduled occurrence with room and an eligible member. A duplicate "
        "booking is refused. Next, say the class date and time after a saved "
        "booking.",
    ),
    (
        ("cash", "expense", "salary", "လစာ", "အသုံး", "ငွေသား", "income", "revenue", "ဝင်ငွေ"),
        "Money operations. Reads: search_expenses, search_salary_periods, "
        "search_cash_sessions, get_gym_dashboard. Dashboard totals use the gym "
        "timezone and include financial amounts only when this staff member "
        "has reports.financial. Writes wait for confirmation and keep the "
        "existing approval rules: expenses, salary periods, and cash sessions. "
        "A submitted salary or expense is not paid or posted until the "
        "required role approves it.",
    ),
    (
        ("stock", "inventory", "retail", "sku", "လက်ကျန်", "အရောင်း"),
        "Inventory and retail. Reads: search_sales. Writes wait for "
        "confirmation: create_product, adjust_stock, create_retail_sale, "
        "submit_retail_sale, return_retail_sale, void_retail_sale. On-hand "
        "quantity is the sum of stock movements. A return follows the posted "
        "sale. Next, say the resulting stock after a saved sale or adjustment.",
    ),
    (
        ("announcement", "ကြေညာ", "check-in", "check in", "ဝင်ရောက်"),
        "Announcements and check-in. Read: search_announcements. Status is "
        "DRAFT, SCHEDULED, PUBLISHED, CANCELLED, or EXPIRED. Writes wait for "
        "confirmation: create_announcement, update_announcement, "
        "publish_announcement, cancel_announcement, create_manual_check_in, "
        "check_out_check_in. Publishing does not mean the member inbox has "
        "already been delivered. A check-in still follows membership eligibility.",
    ),
    (
        ("staff", "role", "permission", "ဝန်ထမ်း", "အခန်းကဏ္ဍ"),
        "Staff and gym settings. Writes wait for confirmation: "
        "update_staff_profile, create_role, update_role, archive_role, "
        "update_gym_profile. Permissions on the signed-in membership decide "
        "which tool is offered. The server checks them again before a write.",
    ),
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
    state["next"] = _next_for(tool, state)


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
    if not reference:
        state["next"] = _next_for(tool, state)
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
    state["next"] = _next_for(tool, state)


def same_created_member(state: Mapping[str, str], payload: Mapping[str, object]) -> bool:
    """A matching name is not the same operation.

    A second member may share a name. Replay protection belongs to the
    confirmed step and its execution identity, not to the person name.
    """

    del state, payload
    return False


def direct_reply(text: str, state: Mapping[str, str]) -> str | None:
    """Answer a stored fact without another model call."""

    dated = stored_date_reply(text, state)
    if dated is not None:
        return dated
    number = _number_reply(text, state)
    if number is not None:
        return number
    if not any(word in text or word in text.casefold() for word in _NEXT_WORDS):
        return None
    return _next_sentence(state, speaks_burmese(text))


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


_PT_INTENT = re.compile(r"(?<![a-z0-9])pt(?![a-z0-9])")


def remember_intent(state: dict[str, str], text: str) -> None:
    """Keep the operation the staff named. A later date must not drop it.

    A member lookup stores the person on the same state. That fact is not a
    switch from personal training to a membership sale. A request that names
    membership, class, and PT together stays on member registration first.
    """

    if short_reply_kind(text) or _date_followup(text):
        return
    if _resumes(text) and state.get("parked"):
        state["workflow"] = state["parked"]
        state["parked"] = ""
        return
    if information_question(text):
        return
    folded = text.casefold()
    wants_pt = bool(_PT_INTENT.search(folded) or "နည်းပြ" in text)
    wants_class = bool(re.search(r"\bclass\b", folded) or "အတန်း" in text)
    wants_member = "member" in folded or "အဖွဲ့ဝင်" in text
    wants_membership = "membership" in folded or "အသင်းဝင်" in text
    if wants_member and (wants_class or wants_pt):
        parts = ["member", "membership"]
        if wants_class:
            parts.append("class")
        if wants_pt:
            parts.append("pt")
        state["goals"] = ",".join(parts)
        if not state.get("member_id"):
            _switch_workflow(state, "member")
        return
    if state.get("goals") and not (wants_pt or wants_class or wants_member or wants_membership):
        return
    if _registers_member(text) and not state.get("member_id"):
        _switch_workflow(state, "member")
        return
    if wants_pt:
        _switch_workflow(state, "pt")
        return
    if "ငွေပေး" in text or "payment" in folded or "ပြန်အမ်း" in text:
        _switch_workflow(state, "payment")
        return
    if wants_membership:
        _switch_workflow(state, "membership")
        return
    if wants_class and state.get("member_id"):
        _switch_workflow(state, "class")


_RESUMES = (
    "အရင်ဟာ ဆက်လုပ်",
    "ဆက်လုပ်",
    "continue that",
    "go back",
    "register her",
    "register him",
)


def _resumes(text: str) -> bool:
    folded = text.casefold()
    return any(phrase in text or phrase in folded for phrase in _RESUMES)


def _registers_member(text: str) -> bool:
    folded = text.casefold()
    if "register" in folded or "စာရင်းသွင်း" in text:
        return True
    return "new member" in folded or "အဖွဲ့ဝင်အသစ်" in text or "အဖွဲ့ဝင် အသစ်" in text


def _switch_workflow(state: dict[str, str], workflow: str) -> None:
    """Move to another operation and keep the previous one for a later return."""

    current = state.get("workflow") or ""
    if current and current != workflow and not state.get("parked"):
        state["parked"] = current
    state["workflow"] = workflow


def _open_registration(state: Mapping[str, str]) -> bool:
    return bool(state.get("member_name")) and not state.get("member_id")


def _plan_matches(state: Mapping[str, str], row: Mapping[str, Any]) -> bool:
    stored = state.get("plan_code") or ""
    found = _text(row.get("code"))
    return not stored or not found or stored.casefold() == found.casefold()


def _kept_start(state: Mapping[str, str]) -> bool:
    return bool(state.get("start_date")) and state.get("workflow") not in {"", "membership"}


_PERSON_NAME = re.compile(r"\b([A-Z][a-z]+(?:[ '-][A-Z][a-z]+)+)\b")
_PLAN_CODE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b")
_DATE_QUESTIONS = (
    "which date",
    "which start date",
    "what date",
    "what start date",
    "ဘယ်ရက်လဲ",
    "ဘယ်ရက်က စမလဲ",
    "ဘယ်နေ့လဲ",
)


def remember_facts(state: dict[str, str], text: str, today: date) -> None:
    """Keep a name, plan code, and calendar day the staff already gave."""

    from apps.assistant.lookups import calendar_day

    if short_reply_kind(text) or asks_stored_date(text):
        return
    found = calendar_day(text, today)
    if isinstance(found, date) and not information_question(text):
        state["start_date"] = found.isoformat()
    if not state.get("member_id") and not state.get("member_name"):
        named = _PERSON_NAME.search(text)
        if named is not None:
            state["member_name"] = named.group(1)
    if not state.get("plan_id"):
        code = _PLAN_CODE.search(text)
        if code is not None:
            state["plan_code"] = code.group(1)


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


def _date_followup(text: str) -> bool:
    """A date or a date question, without a new operation in the same message."""

    from apps.assistant.lookups import calendar_day

    if asks_stored_date(text):
        return True
    if _operation_mention(text):
        return False
    folded = text.casefold()
    if any(
        token in text
        for token in (
            "ဒီနေ့",
            "ယနေ့",
            "မနက်ဖြန်",
            "နက်ဖြန်",
            "မနေ့",
            "မနေ့က",
            "နောက်တစ်ပတ်",
            "နောက်အပတ်",
        )
    ):
        return True
    if re.search(r"\b(today|tomorrow|yesterday|next week)\b", folded):
        return True
    return isinstance(calendar_day(text, date.today()), date)


def _operation_mention(text: str) -> bool:
    folded = text.casefold()
    if _PT_INTENT.search(folded) or "နည်းပြ" in text:
        return True
    if re.search(r"\bclass\b", folded) or "အတန်း" in text:
        return True
    if "member" in folded or "အဖွဲ့ဝင်" in text:
        return True
    if "membership" in folded or "အသင်းဝင်" in text:
        return True
    if "payment" in folded or "ငွေပေး" in text or "ပြန်အမ်း" in text:
        return True
    if "plan" in folded or "အစီအစဉ်" in text:
        return True
    return _PLAN_CODE.search(text) is not None


def capability_brief(text: str, state: Mapping[str, str]) -> str:
    asked = information_question(text)
    blob = text if asked else f"{text} {state.get('workflow', '')} {state.get('next', '')}"
    folded = blob.casefold()
    card = (
        "Relevant operation. Use a registered read before answering a stored "
        "record. A write is only proposed until the server confirms it."
    )
    for words, description in _CAPABILITIES:
        if any(word in folded or word in blob for word in words):
            card = "Relevant operation. " + description
            break
    if asked:
        return (
            card + " Answer only this question from a read tool or the live gym brief. "
            "Leave the stored name, date, plan, and workflow in place."
        )
    goals = state.get("goals") or ""
    if "class" in goals or "pt" in goals:
        card += (
            " This onboarding is separate steps: register the member, then sell "
            "the named membership, then class enrollment, then PT. Do not ask "
            "again for a name, date, or plan already stored. Do not invent a "
            "class, trainer, package, or schedule. Missing class or PT details "
            "do not block member registration or the membership sale."
        )
    return card


_BRIEF_SKIP = frozenset({"verified", "next", "goals", "workflow", "parked"})


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


def dashboard_arguments(
    payload: Mapping[str, object],
    text: str,
    today: date,
) -> dict[str, object]:
    updated = dict(payload)
    replacement = _named_dashboard_period(text, today)
    if replacement is None:
        return updated
    if "range" in replacement:
        updated.pop("date_from", None)
        updated.pop("date_to", None)
    else:
        updated.pop("range", None)
    updated.update(replacement)
    return updated


def _named_dashboard_period(text: str, today: date) -> dict[str, str] | None:
    """Use the month or day the staff named, including over a model range."""

    folded = text.casefold()
    if "နောက်လ" in text or "next month" in folded:
        return {"range": "next_month"}
    if "ဒီလ" in text or "this month" in folded or "နောက်တစ်လ" in text:
        return {"range": "this_month"}
    if "မနေ့က" in text or re.search(r"\byesterday\b", folded):
        day = (today - timedelta(days=1)).isoformat()
        return {"date_from": day, "date_to": day}
    if "မနက်ဖြန်" in text or "နက်ဖြန်" in text or re.search(r"\btomorrow\b", folded):
        day = (today + timedelta(days=1)).isoformat()
        return {"date_from": day, "date_to": day}
    if any(word in text for word in ("ဒီနေ့", "ယနေ့")) or re.search(r"\btoday\b", folded):
        return {"range": "today"}
    return None


def selling_membership(text: str, state: Mapping[str, str] | None = None) -> bool:
    folded = text.casefold()
    if any(
        word in folded or word in text
        for word in ("inactive", "all plans", "plan catalog", "which plan", "ပိတ်ထား")
    ):
        return False
    if state and state.get("next") == "create_membership":
        return True
    phrases = (
        "assign",
        "new membership",
        "create membership",
        "sell a membership",
        "membership sale",
        "အသင်းဝင်ဖွင့်",
        "အသင်းဝင် တပ်",
        "အစီအစဉ်တပ်",
        "membership ထည့်",
        "plan ထည့်",
    )
    return any(phrase in folded or phrase in text for phrase in phrases)


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


def _next_for(tool: str, state: Mapping[str, str]) -> str:
    """A saved write does not choose the staff member's next operation."""

    del tool, state
    return ""


def _number_reply(text: str, state: Mapping[str, str]) -> str | None:
    number = str(state.get("member_number") or "")
    name = str(state.get("member_name") or "")
    if not number:
        return None
    folded = text.casefold()
    asks = (
        "member number" in folded
        or "အဖွဲ့ဝင်နံပါတ်" in text
        or ("နံပါတ်" in text and ("အဖွဲ့ဝင်" in text or "member" in folded))
    )
    if not asks:
        return None
    if (
        name
        and name.casefold() not in folded
        and not ("အဖွဲ့ဝင်နံပါတ်" in text or "member number" in folded)
    ):
        return None
    if speaks_burmese(text):
        who = f"{name} ရဲ့ " if name else ""
        return f"{who}အဖွဲ့ဝင်နံပါတ်က {number} ပါ။"
    who = f"{name}'s " if name else "The "
    return f"{who}member number is {number}."


def _next_sentence(state: Mapping[str, str], burmese: bool) -> str:
    nxt = state.get("next") or ""
    name = state.get("member_name") or ""
    number = state.get("member_number") or ""
    if nxt == "create_membership" and (name or number):
        if burmese:
            return f"{name} ({number}) ကို အသင်းဝင် အစီအစဉ် တပ်ဆင်နိုင်ပါတယ်။ ဘယ်အစီအစဉ်လဲ။".strip()
        return f"A membership can be assigned to {name} ({number}). Which active plan?".strip()
    if nxt == "create_payment":
        if burmese:
            return "အသင်းဝင်ကို သိမ်းပြီးပါပြီ။ ငွေပေးချေမှု မတင်ရသေးပါ။"
        return "The membership is stored. Payment has not been recorded."
    if nxt == "class":
        if burmese:
            return "အသင်းဝင်ကို သိမ်းပြီးပါပြီ။ အတန်းက သီးခြားပါ။ ဘယ်အတန်းနဲ့ ဘယ်အချိန်လဲ။"
        return "The membership is stored. Class enrollment is separate. Which class and schedule?"
    if nxt == "pt":
        if burmese:
            return "PT က သီးခြားပါ။ ဘယ်ပက်ကေ့နဲ့ ဘယ်သင်တန်းဆရာလဲ။"
        return "PT is separate. Which package and trainer?"
    if nxt == "approval":
        if burmese:
            return "တင်ပြီးပါပြီ။ ခွင့်ရှိတဲ့ ဝန်ထမ်းက အတည်ပြုဖို့ ကျန်ပါသေးတယ်။"
        return "It is submitted. An authorized staff member still has to approve it."
    if state.get("last_outcome") == "saved":
        return _done_sentence(state, burmese)
    if burmese:
        return "နောက်တစ်ဆင့်အတွက် လိုအပ်တဲ့ အချက်ကို ပြောပါ။"
    return "Tell me the one fact still needed for the next step."


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
    if state.get("next") == "approval" or state.get("last_tool") in _SUBMITTED:
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

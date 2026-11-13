"""Deterministic function calls for the Playwright assistant.

The running API still executes the registered tools, proposals, and writes.
This module never calls Gemini and is selected only by the E2E settings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_UNGROUNDED = "Invented Person paid 99999 on 1999-01-01."
_REVIEW = "Review the change before confirming."
_WRITES = (
    ("create a membership plan", "create_plan"),
    ("create a membership for", "create_membership"),
    ("create a payment draft", "create_payment"),
    ("submit the payment", "submit_payment"),
    ("approve the payment", "approve_payment"),
)
_FIELD = re.compile(r"\b([a-z_]+)=([A-Za-z0-9_.-]+)")
_QUOTED = re.compile(r"\b([a-z_]+)='([^']*)'")


def scripted_response(body: Mapping[str, object]) -> dict[str, object]:
    """Return one generateContent body for the latest staff instruction."""

    contents = body.get("contents")
    text = _staff_text(contents if isinstance(contents, list) else [])
    if "simulate provider failure" in text.casefold():
        from apps.assistant.configuration import ProviderFailure
        from apps.assistant.provider import ProviderCallError

        raise ProviderCallError(ProviderFailure.QUOTA)
    if text.casefold().strip(" .?") == "what did the member pay":
        return _text("Which member? Tell me the member number.")
    responses = _responses(contents if isinstance(contents, list) else [])
    if text.startswith("The change is saved."):
        return _text("The change is complete.")
    if text.startswith("The booking was not saved."):
        return _text("That booking was not saved.")
    if _write_answered(text, responses):
        return _text(_REVIEW)
    call = _next_call(text, responses)
    if call is None:
        clarification = _clarification(text, responses)
        if clarification is not None:
            return _text(clarification)
        return _text(_UNGROUNDED)
    name, arguments = call
    return _call(name, arguments)


def _clarification(text: str, responses: Mapping[str, Mapping[str, object]]) -> str | None:
    """Ask when the scripted search found more than one matching record."""

    folded = text.casefold()
    if "named yoga" in folded and "search_membership_plans" in responses:
        codes = _strings(responses["search_membership_plans"], "code")
        if len(codes) > 1:
            return f"Which plan code? {' '.join(codes)}"
    if "what did " in folded and " pay" in folded and "search_members" in responses:
        numbers = _strings(responses["search_members"], "member_number")
        if len(numbers) > 1:
            return f"Which member? {' '.join(numbers)}"
    return None


def _strings(body: Mapping[str, object], key: str) -> tuple[str, ...]:
    values: list[str] = []
    for row in _rows(body):
        value = row.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _next_call(
    text: str,
    responses: dict[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]] | None:
    folded = text.casefold()
    if "approve the payment" in folded:
        if "approve_payment" in responses:
            return None
        return _payment_action("approve_payment", text, responses)
    if "submit the payment" in folded:
        if "submit_payment" in responses:
            return None
        return _payment_action("submit_payment", text, responses)
    if "create a payment draft" in folded:
        return _create_payment(text)
    if "create a membership plan" in folded:
        return _create_plan(text)
    if "create a membership for" in folded:
        return _create_membership(text, responses)
    if re.search(r"\bcreate a member\b", folded):
        return _create_member(text)
    if "gym_id=" in folded:
        if "search_members" in responses:
            return None
        return ("search_members", {"gym_id": _value(text, "gym_id")})
    if "member_id=" in folded:
        if "get_member" in responses:
            return None
        return ("get_member", {"member_id": _value(text, "member_id")})
    if "what did " in folded and " pay" in folded:
        return _named_member(text, responses)
    if "named yoga" in folded:
        if "search_membership_plans" not in responses:
            return ("search_membership_plans", {"name": "Yoga"})
        return None
    reads = _read_sequence(folded)
    for name, arguments in reads:
        if name not in responses:
            return name, arguments
    return None


def _write_answered(text: str, responses: Mapping[str, Mapping[str, object]]) -> bool:
    folded = text.casefold()
    for phrase, name in _WRITES:
        if phrase in folded and name in responses:
            return True
    return bool(re.search(r"\bcreate a member\b", folded) and "create_member" in responses)


def _read_sequence(folded: str) -> tuple[tuple[str, dict[str, object]], ...]:
    calls: list[tuple[str, dict[str, object]]] = []
    if "search members" in folded or "အဖွဲ့ဝင်" in folded:
        calls.append(("search_members", {}))
    if "search payments" in folded or "ငွေပေး" in folded:
        calls.append(("search_payments", {}))
    if "membership plan" in folded or "inactive plan" in folded:
        calls.append(("search_membership_plans", {}))
    if "class occurrence" in folded or "အတန်း" in folded:
        calls.append(("search_class_occurrences", {}))
    if "class booking" in folded or "ဘိုကင်" in folded:
        calls.append(("search_bookings", {}))
    if "retail sale" in folded or "အရောင်း" in folded:
        calls.append(("search_sales", {}))
    if "expense" in folded or "အသုံးစရိတ်" in folded:
        calls.append(("search_expenses", {}))
    if "salary period" in folded or "လစာ" in folded:
        calls.append(("search_salary_periods", {}))
    if "cash session" in folded or "ငွေသား" in folded:
        calls.append(("search_cash_sessions", {}))
    if "pt commission" in folded or "ကော်မရှင်" in folded:
        calls.append(("search_pt_commissions", {}))
    return tuple(calls)


def _create_payment(text: str) -> tuple[str, dict[str, object]] | None:
    arguments: dict[str, object] = {
        "purpose": _value(text, "purpose"),
        "description": _quoted(text, "description"),
        "amount": _value(text, "amount"),
        "currency": _value(text, "currency"),
        "method": _value(text, "method"),
        "business_date": _value(text, "business_date"),
    }
    if any(value == "" for value in arguments.values()):
        return None
    return "create_payment", arguments


def _create_member(text: str) -> tuple[str, dict[str, object]] | None:
    arguments: dict[str, object] = {
        "first_name": _value(text, "first_name"),
        "last_name": _value(text, "last_name"),
        "join_date": _value(text, "join_date"),
    }
    if any(value == "" for value in arguments.values()):
        return None
    return "create_member", arguments


def _create_plan(text: str) -> tuple[str, dict[str, object]] | None:
    arguments: dict[str, object] = {
        "code": _value(text, "code"),
        "name": _value(text, "name"),
        "duration_value": _value(text, "duration_value"),
        "duration_unit": _value(text, "duration_unit"),
        "price": _value(text, "price"),
    }
    if any(value == "" for value in arguments.values()):
        return None
    return "create_plan", arguments


def _create_membership(
    text: str,
    responses: dict[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]] | None:
    if "search_members" not in responses:
        return "search_members", {"search": _quoted(text, "member_name")}
    if "search_membership_plans" not in responses:
        return "search_membership_plans", {"code": _value(text, "plan_code")}
    member_id = _first_id(responses["search_members"])
    plan_id = _first_id(responses["search_membership_plans"])
    start_date = _value(text, "start_date")
    if member_id == "" or plan_id == "" or start_date == "":
        return None
    return "create_membership", {
        "member_id": member_id,
        "plan_id": plan_id,
        "start_date": start_date,
    }


def _named_member(
    text: str,
    responses: dict[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]] | None:
    if "search_members" in responses:
        return None
    match = re.search(r"\bwhat did (.+?) pay\b", text, re.IGNORECASE)
    if match is None:
        return "search_members", {}
    return "search_members", {"search": match.group(1).strip(" .?")}


def _payment_action(
    tool_name: str,
    text: str,
    responses: dict[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]] | None:
    if "search_payments" not in responses:
        return "search_payments", {}
    description = _quoted(text, "description")
    row = _row_by_description(responses["search_payments"], description)
    if row is None:
        return None
    payment_id = str(row.get("id", ""))
    if payment_id == "":
        return None
    if tool_name == "submit_payment":
        return tool_name, {"payment_id": payment_id}
    amount = str(row.get("amount", ""))
    return tool_name, {
        "payment_id": payment_id,
        "record_id": payment_id,
        "amount": amount,
    }


def _staff_text(contents: list[object]) -> str:
    texts: list[str] = []
    for item in contents:
        if not isinstance(item, Mapping) or item.get("role") != "user":
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    for text in reversed(texts):
        if not text.startswith("Call the stored read"):
            return text
    return texts[-1] if texts else ""


def _responses(contents: list[object]) -> dict[str, Mapping[str, object]]:
    found: dict[str, Mapping[str, object]] = {}
    for item in contents:
        if not isinstance(item, Mapping):
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            response = part.get("functionResponse")
            if not isinstance(response, Mapping):
                continue
            name = response.get("name")
            body = response.get("response")
            if isinstance(name, str) and isinstance(body, Mapping):
                found[name] = body
    return found


def _rows(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    result = body.get("result")
    if not isinstance(result, Mapping):
        return []
    listed = result.get("results")
    if not isinstance(listed, list):
        return []
    return [row for row in listed if isinstance(row, Mapping)]


def _first_id(body: Mapping[str, object]) -> str:
    rows = _rows(body)
    if not rows:
        return ""
    value = rows[0].get("id")
    return value if isinstance(value, str) else ""


def _row_by_description(
    body: Mapping[str, object],
    description: str,
) -> Mapping[str, object] | None:
    for row in _rows(body):
        if row.get("description") == description:
            return row
    return None


def _value(text: str, name: str) -> str:
    for key, value in _FIELD.findall(text):
        if key == name:
            return str(value)
    return ""


def _quoted(text: str, name: str) -> str:
    for key, value in _QUOTED.findall(text):
        if key == name:
            return str(value)
    return _value(text, name)


def _text(text: str) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "candidates": [
            {"content": {"parts": [{"functionCall": {"name": name, "args": arguments}}]}}
        ]
    }

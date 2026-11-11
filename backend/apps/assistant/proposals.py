"""Turn a model tool call into a stored confirmation. This executes nothing."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

from apps.assistant.executor import ApprovalSnapshot, ToolContract
from apps.assistant.registry import ToolDefinition

_CONTROL = frozenset({"confirmed", "gym_id"})
_APPROVAL = frozenset({"record_id", "amount"})
_DEPTH = 6
_WIDTH = 40
_TEXT = 2000


def json_copy(value: object, depth: int = 0) -> object:
    """Keep only JSON values the confirmation row can store."""

    if depth > _DEPTH:
        raise ValueError("nested")
    if value is None or isinstance(value, str | bool):
        if isinstance(value, str) and len(value) > _TEXT:
            raise ValueError("text")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("number")
        return value
    if isinstance(value, list):
        if len(value) > _WIDTH:
            raise ValueError("width")
        return [json_copy(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > _WIDTH:
            raise ValueError("width")
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key == "" or len(key) > 64:
                raise ValueError("key")
            copied[key] = json_copy(item, depth + 1)
        return copied
    raise ValueError("type")


def model_arguments(arguments: Mapping[str, object], *, approval: bool) -> dict[str, object]:
    """Drop control fields. An approval amount never comes from the model."""

    copied = json_copy(dict(arguments))
    if not isinstance(copied, dict):
        raise ValueError("type")
    payload = {
        key: value
        for key, value in copied.items()
        if key.casefold() not in _CONTROL and not (approval and key.casefold() in _APPROVAL)
    }
    return cast(dict[str, object], payload)


def with_snapshot(payload: Mapping[str, object], snapshot: ApprovalSnapshot) -> dict[str, object]:
    """Attach the stored record identity and amount."""

    stored = dict(payload)
    stored.pop("confirmed", None)
    stored.pop("gym_id", None)
    stored["record_id"] = snapshot.record_id
    stored["amount"] = snapshot.amount
    return stored


def declaration(contract: ToolContract) -> dict[str, object]:
    """Describe one tool. Approval identity and amount stay off the model schema."""

    tool = contract.definition
    hidden = _APPROVAL if tool.approval_required else frozenset()
    properties: dict[str, object] = {}
    required: list[str] = []
    if contract.prerequisite is not None:
        for field in contract.prerequisite.fields:
            if field.name in hidden:
                continue
            properties[field.name] = {"type": "string", "description": field.description}
            required.append(field.name)
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def summary_for(
    tool: ToolDefinition,
    payload: Mapping[str, object],
    approval: Mapping[str, str] | None,
) -> str:
    if approval is not None:
        return f"{tool.description}. Record {approval['record_id']}, amount {approval['amount']}."
    amount = payload.get("amount")
    if isinstance(amount, str | int | float) and not isinstance(amount, bool) and amount != "":
        return f"{tool.description}. Amount {amount}."
    return f"{tool.description}."


def display_fields(
    payload: Mapping[str, object],
    *,
    approval: bool,
) -> list[dict[str, str]]:
    hidden = _APPROVAL if approval else frozenset()
    fields: list[dict[str, str]] = []
    for name in sorted(payload):
        if name.casefold() in _CONTROL or name in hidden:
            continue
        text = _display(payload[name])
        if text != "":
            fields.append({"name": name, "value": text})
    return fields


def public_pending(
    raw: object,
    *,
    step_id: object,
    goal_id: object,
    tool_name: str,
) -> dict[str, object] | None:
    """Return the confirmation card stored on the step."""

    if not isinstance(raw, Mapping):
        return None
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        return None
    approval = raw.get("approval")
    pair: dict[str, str] | None = None
    if isinstance(approval, Mapping):
        record_id = approval.get("record_id")
        amount = approval.get("amount")
        if isinstance(record_id, str) and isinstance(amount, str) and record_id and amount:
            pair = {"record_id": record_id, "amount": amount}
    summary = raw.get("summary")
    return {
        "step_id": step_id,
        "goal_id": goal_id,
        "tool_name": tool_name,
        "summary": summary if isinstance(summary, str) and summary else tool_name,
        "fields": display_fields(payload, approval=pair is not None),
        "approval": pair,
    }


def _display(value: object) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool) or value is None:
        text = ""
    elif isinstance(value, int | float):
        text = str(value)
    else:
        text = ""
    return text[:200]

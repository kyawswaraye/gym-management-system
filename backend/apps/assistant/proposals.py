"""Turn a model tool call into a stored confirmation. This executes nothing."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import cast

from apps.assistant.executor import ApprovalSnapshot, ToolContract
from apps.assistant.knowledge import public_tool_label
from apps.assistant.registry import ToolDefinition

_CONTROL = frozenset({"confirmed", "gym_id"})
_APPROVAL = frozenset({"record_id", "amount"})
_DEPTH = 6
_WIDTH = 40
_TEXT = 2000
_SKU = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
_LINE_MONEY = ("discount",)
_UNAVAILABLE = "Product unavailable"


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
            properties[field.name] = {"type": field.kind, "description": field.description}
            if field.required:
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
    fields = display_fields(payload, approval=pair is not None)
    labels = raw.get("labels")
    if isinstance(labels, Mapping):
        for field in fields:
            label = labels.get(field["name"])
            if isinstance(label, str) and label.strip():
                field["value"] = label.strip()
        fields.extend(_sale_line_fields(payload, labels))
    else:
        fields.extend(_sale_line_fields(payload, {}))
    label = public_tool_label(tool_name)
    return {
        "step_id": step_id,
        "goal_id": goal_id,
        "tool_name": label,
        "summary": summary if isinstance(summary, str) and summary else label,
        "fields": fields,
        "approval": pair,
    }


def _sale_line_fields(
    payload: Mapping[str, object],
    labels: Mapping[str, object],
) -> list[dict[str, str]]:
    """Show stored sale lines. The label is the one saved with the proposal."""

    lines = payload.get("lines")
    if not isinstance(lines, list):
        return []
    fields: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            continue
        prefix = f"lines.{index}"
        stored = labels.get(f"{prefix}.product_id")
        fields.append({"name": f"{prefix}.product", "value": _stored_product(stored)})
        if "quantity" in line:
            quantity = _stored_scalar(line.get("quantity"))
            if quantity != "":
                fields.append({"name": f"{prefix}.quantity", "value": quantity})
        for key in _LINE_MONEY:
            if key not in line:
                continue
            amount = _stored_scalar(line.get(key))
            if amount != "":
                fields.append({"name": f"{prefix}.{key}", "value": amount})
    return fields


def _stored_product(value: object) -> str:
    if not isinstance(value, str):
        return _UNAVAILABLE
    text = " ".join(value.split())
    name, marker, sku = text.rpartition(" (")
    if marker == "" or not text.endswith(")"):
        return _UNAVAILABLE
    code = sku[:-1]
    if not name.strip() or _SKU.fullmatch(code) is None:
        return _UNAVAILABLE
    return f"{name.strip()} ({code})"


def _stored_scalar(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:200]
    if isinstance(value, int | float):
        return str(value)
    return ""


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

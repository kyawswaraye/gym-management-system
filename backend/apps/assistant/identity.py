from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from uuid import UUID

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def canonical_payload(payload: Mapping[str, object]) -> str:
    """Stable JSON for a confirmed payload. Non-JSON values are rejected."""

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Confirmed payload must be JSON data.") from exc


def payload_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("ascii")).hexdigest()


def execution_identity(
    *,
    gym_id: UUID,
    goal_id: UUID,
    step_id: UUID,
    tool_name: str,
    attempt_number: int,
    payload: Mapping[str, object],
) -> str:
    """Identity of one confirmed attempt. The gym id is the server argument."""

    if _TOOL_NAME.fullmatch(tool_name) is None:
        raise ValueError("Tool name is not a valid identifier.")
    if attempt_number < 1:
        raise ValueError("Attempt number starts at 1.")
    material = "\n".join(
        (
            str(gym_id),
            str(goal_id),
            str(step_id),
            tool_name,
            str(attempt_number),
            canonical_payload(payload),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

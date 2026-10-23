"""One-line JSON for production diagnostic logs."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from apps.common.observability.redaction import redact_text, redact_value

_EXTRA = (
    "event",
    "request_id",
    "route",
    "method",
    "status",
    "duration_ms",
    "service",
    "actor_id",
    "gym_id",
    "outcome",
)


class JsonLogFormatter(logging.Formatter):
    """Format one diagnostic record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "service": "api",
        }
        for key in _EXTRA:
            if key in payload:
                continue
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        redacted = redact_value(payload)
        if not isinstance(redacted, dict):
            redacted = {"message": "[redacted]", "service": "api", "logger": record.name}
        line = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if "\n" in line or "\r" in line:
            return '{"logger":"gym.http","message":"[redacted]","service":"api"}'
        return line

from __future__ import annotations

from typing import Any

from rest_framework.serializers import Serializer

from apps.common.api.exceptions import RequestRejected


def validated(serializer: Serializer[Any]) -> dict[str, Any]:
    """Return validated data, or a stable 400 that does not echo secrets."""

    if serializer.is_valid():
        return dict(serializer.validated_data)
    fields: dict[str, list[str]] = {}
    for key, messages in serializer.errors.items():
        if isinstance(messages, list):
            fields[str(key)] = [str(item) for item in messages]
        else:
            fields[str(key)] = [str(messages)]
    raise RequestRejected(fields)

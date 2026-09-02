from __future__ import annotations

from typing import Any

from rest_framework.response import Response


def success_response(
    data: Any = None,
    *,
    message: str = "Success",
    status: int = 200,
    meta: dict[str, Any] | None = None,
) -> Response:
    body: dict[str, Any] = {"success": True, "data": data}
    if meta is not None:
        body["meta"] = meta
    body["message"] = message
    return Response(body, status=status)

"""Safe request completion events. Audit evidence is a separate store."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_LOGGER = logging.getLogger("gym.http")


def normalized_route(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", None)
    if not isinstance(route, str) or route == "":
        return "unmatched"
    if len(route) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in route):
        return "unmatched"
    if any(token in route for token in ("?", " ", "://", "\n", "\r")):
        return "unmatched"
    return route


def request_log_level(status: int) -> int:
    if status >= 500:
        return logging.ERROR
    if status == 429 or (status >= 400 and status not in {401, 403, 404}):
        return logging.WARNING
    return logging.INFO


def _identifier(request: HttpRequest, name: str) -> str | None:
    value = getattr(request, name, None)
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        return None
    return value


class RequestLogMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self.get_response(request)
        if getattr(settings, "STRUCTURED_REQUEST_LOG", False):
            self._log(request, response, started)
        return response

    def _log(self, request: HttpRequest, response: HttpResponse, started: float) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        method = request.method if request.method in _METHODS else "OTHER"
        extra: dict[str, object] = {
            "event": "http.request",
            "request_id": str(getattr(request, "request_id", "") or "")[:128],
            "route": normalized_route(request),
            "method": method,
            "status": status,
            "duration_ms": duration_ms,
            "service": "api",
        }
        actor = _identifier(request, "observability_actor_id")
        gym = _identifier(request, "observability_gym_id")
        if actor is not None:
            extra["actor_id"] = actor
        if gym is not None:
            extra["gym_id"] = gym
        _LOGGER.log(request_log_level(status), "request completed", extra=extra)

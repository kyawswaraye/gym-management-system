from __future__ import annotations

import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get("X-Request-ID", "")
        request.request_id = (
            supplied if REQUEST_ID_RE.fullmatch(supplied) else f"req_{uuid.uuid4()}"
        )
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response

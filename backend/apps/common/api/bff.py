from __future__ import annotations

import hmac
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

BFF_SECRET_HEADER = "X-Gym-BFF-Secret"
AUTH_API_PREFIX = "/api/v1/auth/"
TRUSTED_BFF_PATHS = {
    "/api/v1/owners/invitations/accept/",
    "/api/v1/staff/invitations/accept/",
}
MIN_BFF_SECRET_LENGTH = 32


def bff_secret_is_configured(secret: str) -> bool:
    return bool(secret) and len(secret) >= MIN_BFF_SECRET_LENGTH


def request_has_trusted_bff(request: HttpRequest, secret: str) -> bool:
    supplied = request.headers.get(BFF_SECRET_HEADER, "")
    return (
        bff_secret_is_configured(secret)
        and bool(supplied)
        and hmac.compare_digest(secret, supplied)
    )


class BffTrustMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            request.path.startswith(AUTH_API_PREFIX) or request.path in TRUSTED_BFF_PATHS
        ) and not request_has_trusted_bff(request, settings.BFF_SHARED_SECRET):
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "BFF_REQUIRED",
                        "message": "This endpoint accepts only the trusted browser BFF.",
                        "fields": {},
                    },
                    "request_id": getattr(request, "request_id", "req_unknown"),
                },
                status=403,
            )
        return self.get_response(request)

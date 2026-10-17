from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class CodedAPIException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "INVALID_REQUEST"
    default_detail = "The request could not be processed."


class AuthenticationFailed(CodedAPIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "AUTHENTICATION_FAILED"
    default_detail = "Authentication credentials are invalid."


class AccessTokenExpired(AuthenticationFailed):
    default_code = "ACCESS_TOKEN_EXPIRED"
    default_detail = "The access token has expired."


def _flatten_fields(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items()}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    from rest_framework.views import exception_handler

    response = exception_handler(exc, context)
    request = context.get("request")
    request_id = getattr(request, "request_id", "req_unknown")
    if response is None:
        logger.exception("Unhandled API exception", extra={"request_id": request_id}, exc_info=exc)
        return Response(
            {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred.",
                    "fields": {},
                },
                "request_id": request_id,
            },
            status=500,
        )
    code = "INVALID_REQUEST"
    message = "The request could not be processed."
    fields: dict[str, Any] = {}
    if isinstance(exc, APIException):
        code = str(exc.get_codes())
        if isinstance(exc.detail, ErrorDetail):
            code = str(exc.detail.code).upper()
            message = str(exc.detail)
            extra_fields = getattr(exc, "error_fields", None)
            if isinstance(extra_fields, dict):
                fields = extra_fields
        else:
            fields = _flatten_fields(response.data)
    return Response(
        {
            "success": False,
            "error": {"code": code, "message": message, "fields": fields},
            "request_id": request_id,
        },
        status=response.status_code,
        headers=_exception_headers(exc, response),
    )


def _exception_headers(exc: Exception, response: Response) -> dict[str, str]:
    """Copy DRF headers, then emit Retry-After only for a present integer wait."""

    headers = {str(key): str(value) for key, value in response.headers.items()}
    wait = getattr(exc, "wait", None)
    if isinstance(wait, bool) or (isinstance(wait, int) and wait < 0):
        return {key: value for key, value in headers.items() if key.lower() != "retry-after"}
    if isinstance(wait, int):
        headers = {key: value for key, value in headers.items() if key.lower() != "retry-after"}
        headers["Retry-After"] = str(wait)
    return headers

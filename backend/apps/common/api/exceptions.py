from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.response import Response


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
        else:
            fields = _flatten_fields(response.data)
    return Response(
        {
            "success": False,
            "error": {"code": code, "message": message, "fields": fields},
            "request_id": request_id,
        },
        status=response.status_code,
        headers=response.headers,
    )

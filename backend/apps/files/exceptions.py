from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ErrorDetail

from apps.common.api.exceptions import CodedAPIException


class RateLimited(CodedAPIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "RATE_LIMITED"
    default_detail = "Too many requests."


class FileRequestError(CodedAPIException):
    """Stable file error that can name one field without changing the code."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        fields: dict[str, list[str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.error_fields = fields or {}
        if retry_after is not None:
            self.wait = retry_after
        super().__init__(detail=ErrorDetail(message, code))


class ObjectMissing(Exception):
    """The object is not in the bucket."""


class ObjectStatInvalid(Exception):
    """A successful HEAD did not include a usable Content-Length."""


class ObjectTooLarge(Exception):
    """The object exceeds the bounded read."""


class ObjectStoreUnavailable(Exception):
    """Storage failed without a client-facing provider message."""


class ScannerUnavailable(Exception):
    """The scanner timed out or returned an engine error."""


class ImageRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

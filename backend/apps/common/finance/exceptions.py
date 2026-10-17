from rest_framework import status

from apps.common.api.exceptions import CodedAPIException


class IdempotencyInProgress(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "IDEMPOTENCY_IN_PROGRESS"
    default_detail = "This idempotency key is already being processed."


class IdempotencyKeyExpired(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "IDEMPOTENCY_KEY_EXPIRED"
    default_detail = "This idempotency key has expired."


class IdempotencyRequestInvalid(CodedAPIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "INVALID_REQUEST"
    default_detail = "The idempotency request is not valid."


class CacheableIdempotentFailure(CodedAPIException):
    """Deterministic finance-domain failure that may be replayed for this key.

    Authentication, authorization, throttling, and serializer validation must
    not use this marker. Those decisions can change without the request body
    changing, and throttling or authentication responses carry headers this
    store does not replay. Subclasses are rendered through the API exception
    handler with the live request, so the stored body keeps that request id.
    """

    cacheable_idempotent_failure = True


class FinanceStateConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "FINANCE_STATE_CONFLICT"
    default_detail = "The finance record cannot accept this command."


class IdempotencyResponseTooLarge(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "IDEMPOTENCY_RESPONSE_TOO_LARGE"
    default_detail = "The idempotent response exceeds the stored size limit."

from rest_framework import status

from apps.common.api.exceptions import CodedAPIException
from apps.common.finance.exceptions import CacheableIdempotentFailure


class ClassTypeConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CLASS_TYPE_CONFLICT"
    default_detail = "The class type conflicts with an existing class type."

    def __init__(self, field: str) -> None:
        self.error_fields = {field: ["This value is already in use."]}
        super().__init__()


class ClassTypeInactive(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "CLASS_TYPE_INACTIVE"
    default_detail = "The class type is not active."


class TrainerNotEligible(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "TRAINER_NOT_ELIGIBLE"
    default_detail = "The staff profile is not an active trainer."


class ScheduleTooLarge(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "SCHEDULE_TOO_LARGE"
    default_detail = "The schedule materializes more than 400 occurrences."


class ScheduleNoMatches(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "SCHEDULE_NO_MATCHES"
    default_detail = "The schedule range contains no matching weekday."


class ScheduleConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "SCHEDULE_CONFLICT"
    default_detail = "The schedule overlaps another scheduled class."


class OccurrenceStateConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "OCCURRENCE_STATE_CONFLICT"
    default_detail = "The class occurrence cannot accept this command."


class ClassFull(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CLASS_FULL"
    default_detail = "The class occurrence has no remaining capacity."


class ClassCutoff(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CLASS_CUTOFF"
    default_detail = "The class cancellation cutoff has passed."


class BookingStateConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "BOOKING_STATE_CONFLICT"
    default_detail = "The booking cannot accept this command."

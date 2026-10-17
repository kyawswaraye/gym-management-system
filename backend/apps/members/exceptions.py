from rest_framework import status

from apps.common.api.exceptions import CodedAPIException


class MemberConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "MEMBER_CONFLICT"
    default_detail = "The member conflicts with an existing member."


class MembershipPlanConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "MEMBERSHIP_PLAN_CONFLICT"
    default_detail = "The membership plan conflicts with an existing plan."


class MembershipOverlap(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "MEMBERSHIP_OVERLAP"
    default_detail = (
        "The membership dates overlap another pending, active, or suspended membership."
    )


class MembershipStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "MEMBERSHIP_STATE_CONFLICT"
    default_detail = "The membership cannot change from its current state."


class CheckInCodeInvalid(CodedAPIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "CHECK_IN_CODE_INVALID"
    default_detail = "The check-in code is not valid."


class CheckInIneligible(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CHECK_IN_INELIGIBLE"
    default_detail = "This member does not have an eligible active membership."


class CheckInDuplicate(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CHECK_IN_DUPLICATE"
    default_detail = "This member was already checked in during the duplicate window."


class CheckInOpen(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CHECK_IN_OPEN"
    default_detail = "This member is already checked in. Check out before checking in again."


class DisplayDeviceStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "DISPLAY_DEVICE_STATE_CONFLICT"
    default_detail = "The display device cannot change from its current state."


class DeviceStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "DEVICE_STATE_CONFLICT"
    default_detail = "The device cannot change from its current state."


class CheckInStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CHECK_IN_STATE_CONFLICT"
    default_detail = "The check-in cannot change from its current state."

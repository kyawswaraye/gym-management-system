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

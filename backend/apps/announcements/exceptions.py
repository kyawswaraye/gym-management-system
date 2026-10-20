from rest_framework import status

from apps.common.api.exceptions import CodedAPIException


class AnnouncementStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "ANNOUNCEMENT_STATE_CONFLICT"
    default_detail = "The announcement cannot accept this command."

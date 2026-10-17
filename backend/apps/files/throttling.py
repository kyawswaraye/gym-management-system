from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle


class MembershipWindowThrottle(SimpleRateThrottle):
    """Count requests for one gym membership across an explicit second window."""

    def parse_rate(self, rate: str | None) -> tuple[int, int] | tuple[None, None]:
        if rate is None:
            return (None, None)
        count, window = rate.split("/")
        return int(count), int(window)

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        membership = getattr(request, "gym_membership", None)
        if membership is None:
            return None
        ident = str(membership.id)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class UploadIntentThrottle(MembershipWindowThrottle):
    scope = "file_upload_intent"


class FileCompleteThrottle(MembershipWindowThrottle):
    scope = "file_complete"


class FileDownloadThrottle(MembershipWindowThrottle):
    scope = "file_download_url"


class FileDeleteThrottle(MembershipWindowThrottle):
    scope = "file_delete"

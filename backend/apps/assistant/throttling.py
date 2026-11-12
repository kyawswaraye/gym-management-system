"""Per-membership and per-gym limits for assistant routes.

The membership window follows the finance command throttles. The gym window
is separate so one staff member cannot consume another membership's bucket,
and one gym cannot be exhausted only by counting addresses.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.settings import api_settings

from apps.files.throttling import MembershipWindowThrottle


class _Rate(MembershipWindowThrottle):
    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


class AssistantReadThrottle(_Rate):
    scope = "assistant_read"


class AssistantTurnThrottle(_Rate):
    scope = "assistant_turn"


class _GymThrottle(_Rate):
    def get_cache_key(self, request: Request, view: Any) -> str | None:
        gym = getattr(request, "gym", None)
        gym_id = getattr(gym, "id", None)
        if not gym_id:
            return None
        return self.cache_format % {"scope": self.scope, "ident": str(gym_id)}


class AssistantGymReadThrottle(_GymThrottle):
    scope = "assistant_gym_read"


class AssistantGymTurnThrottle(_GymThrottle):
    scope = "assistant_gym_turn"

"""Per-gym AI subscription entitlement.

The browser, the model, and a request body cannot grant this. The gym row
stored for the authenticated session is the only source.
"""

from __future__ import annotations

from uuid import UUID

from rest_framework import status

from apps.common.api.exceptions import CodedAPIException
from apps.tenants.models import Gym


class AssistantNotEntitled(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "AI_NOT_ENTITLED"
    default_detail = "AI Assistant is not included in this gym's subscription."


def gym_ai_entitled(gym_id: UUID) -> bool:
    """Read the stored flag. A stale in-memory value is not accepted."""

    return Gym.objects.filter(pk=gym_id, ai_enabled=True).exists()


def require_gym_ai(gym: Gym) -> None:
    if gym.id is None or not gym_ai_entitled(gym.id):
        raise AssistantNotEntitled()

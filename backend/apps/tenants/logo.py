"""Validate and store the single logo a gym uploads for its workspace."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.audit.services import write_tenant_audit
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymLogo
from apps.tenants.services import _require_active_gym, _require_active_membership

MAX_LOGO_BYTES = 1_048_576
LOGO_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")


def read_logo_upload(upload: Any) -> tuple[bytes, str]:
    content_type = str(getattr(upload, "content_type", "") or "")
    if content_type not in LOGO_CONTENT_TYPES:
        raise serializers.ValidationError({"logo": ["Upload a JPEG, PNG, or WebP logo."]})
    data = upload.read(MAX_LOGO_BYTES + 1)
    if not isinstance(data, (bytes, bytearray)) or not 1 <= len(data) <= MAX_LOGO_BYTES:
        raise serializers.ValidationError(
            {"logo": ["The logo must be a JPEG, PNG, or WebP under 1 MB."]}
        )
    payload = bytes(data)
    if not _matches_type(content_type, payload):
        raise serializers.ValidationError(
            {"logo": ["The file contents do not match the image type."]}
        )
    return payload, content_type


def _matches_type(content_type: str, payload: bytes) -> bool:
    if content_type == "image/jpeg":
        return payload.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    return payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"


@transaction.atomic
def replace_gym_logo(
    *,
    gym_id: uuid.UUID,
    data: bytes,
    content_type: str,
    actor: Any,
    actor_membership_id: uuid.UUID,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> Gym:
    set_gym_context(gym_id=gym_id)
    gym = _require_active_gym(gym_id)
    _require_active_membership(gym_id=gym_id, actor=actor, actor_membership_id=actor_membership_id)
    GymLogo.objects.update_or_create(
        gym=gym,
        defaults={"content_type": content_type, "data": data},
    )
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=actor_membership_id,
        action="GYM_LOGO_UPDATED",
        target_type="Gym",
        target_id=gym.id,
        request_id=request_id,
        old_values={},
        new_values={"content_type": content_type},
        ip_address=ip_address,
        user_agent=user_agent,
        module="gym",
    )
    return gym


@transaction.atomic
def clear_gym_logo(
    *,
    gym_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> Gym:
    set_gym_context(gym_id=gym_id)
    gym = _require_active_gym(gym_id)
    _require_active_membership(gym_id=gym_id, actor=actor, actor_membership_id=actor_membership_id)
    deleted, _counts = GymLogo.objects.filter(pk=gym.id).delete()
    if deleted:
        write_tenant_audit(
            gym=gym,
            actor_user=actor,
            actor_membership_id=actor_membership_id,
            action="GYM_LOGO_REMOVED",
            target_type="Gym",
            target_id=gym.id,
            request_id=request_id,
            old_values={},
            new_values={},
            ip_address=ip_address,
            user_agent=user_agent,
            module="gym",
        )
    return gym

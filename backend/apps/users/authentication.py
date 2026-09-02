from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context, set_identity_context
from apps.tenants.models import Gym, GymMembership, RolePermission

from .models import RefreshSession
from .tokens import decode_access_token


class StaffJWTAuthentication(BaseAuthentication):
    keyword = b"Bearer"

    def authenticate(self, request: Request) -> tuple[Any, dict[str, Any]] | None:
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed()
        try:
            token = parts[1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise AuthenticationFailed() from exc
        claims = decode_access_token(token)
        user_id = uuid.UUID(claims["sub"])
        gym_id = uuid.UUID(claims["gym_id"])
        membership_id = uuid.UUID(claims["gym_membership_id"])
        session_id = uuid.UUID(claims["session_id"])

        set_identity_context(user_id=user_id, request_id=request.request_id)
        try:
            session = RefreshSession.objects.select_related("user").get(
                id=session_id,
                user_id=user_id,
                principal_type=RefreshSession.PrincipalType.STAFF,
                selected_gym_id=gym_id,
                gym_membership_id=membership_id,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                user__is_active=True,
            )
            gym = Gym.objects.get(
                id=gym_id, status=Gym.Status.ACTIVE, subscription_status__in=["TRIAL", "ACTIVE"]
            )
        except (RefreshSession.DoesNotExist, Gym.DoesNotExist) as exc:
            raise AuthenticationFailed() from exc

        set_gym_context(gym_id=gym_id)
        try:
            membership = GymMembership.objects.get(
                id=membership_id,
                gym_id=gym_id,
                user_id=user_id,
                status=GymMembership.Status.ACTIVE,
                authz_version=claims["authz_version"],
            )
        except GymMembership.DoesNotExist as exc:
            raise AuthenticationFailed() from exc
        permission_codes = frozenset(
            RolePermission.objects.filter(
                gym_id=gym_id,
                role__gymmembershiprole__gym_membership=membership,
                role__gymmembershiprole__gym_id=gym_id,
                role__is_active=True,
                permission__is_active=True,
            ).values_list("permission__code", flat=True)
        )
        request.gym = gym
        request.gym_membership = membership
        request.permission_codes = permission_codes
        request.refresh_session = session
        return session.user, claims

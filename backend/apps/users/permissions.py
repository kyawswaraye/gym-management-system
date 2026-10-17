from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.tenants.exceptions import MFARequired
from apps.tenants.models import GymFeatureEntitlement
from apps.users.mfa import has_recent_mfa
from apps.users.models import RefreshSession


class RequiresPermission(BasePermission):
    required_permission = ""

    def has_permission(self, request: Request, view: APIView) -> bool:
        code = getattr(view, "required_permission", self.required_permission)
        return bool(code) and code in getattr(request, "permission_codes", frozenset())


class RequiresPlatformPermission(RequiresPermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        code = getattr(view, "required_permission", self.required_permission)
        return (
            getattr(request, "principal_type", None) == RefreshSession.PrincipalType.PLATFORM
            and code.startswith("platform.")
            and super().has_permission(request, view)
        )


class RequiresPlatformPermissions(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        required = tuple(getattr(view, "required_permissions", ()))
        granted: frozenset[str] = getattr(request, "permission_codes", frozenset())
        return (
            getattr(request, "principal_type", None) == RefreshSession.PrincipalType.PLATFORM
            and bool(required)
            and all(code.startswith("platform.") and code in granted for code in required)
        )


class RequiresEntitlement(BasePermission):
    required_feature = ""

    def has_permission(self, request: Request, view: APIView) -> bool:
        code = getattr(view, "required_feature", self.required_feature)
        gym = getattr(request, "gym", None)
        if not code or gym is None:
            return False
        now = timezone.now()
        return GymFeatureEntitlement.objects.filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=now),
            Q(effective_until__isnull=True) | Q(effective_until__gt=now),
            gym=gym,
            feature__code=code,
            enabled=True,
        ).exists()


class IsActiveOwner(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        membership = getattr(request, "gym_membership", None)
        return bool(
            membership and membership.status == membership.Status.ACTIVE and membership.is_owner
        )


class RequiresRecentMFA(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        allowed = bool(
            getattr(request, "user", None)
            and getattr(request.user, "mfa_enabled", False)
            and has_recent_mfa(getattr(request, "refresh_session", None))
        )
        if not allowed:
            raise MFARequired()
        return True

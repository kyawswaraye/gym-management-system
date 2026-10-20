from __future__ import annotations

from uuid import UUID

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


def _requirement_codes(raw: object) -> tuple[str, ...] | None:
    if isinstance(raw, str) or not isinstance(raw, tuple | list) or len(raw) == 0:
        return None
    codes: list[str] = []
    for code in raw:
        if not isinstance(code, str) or not code:
            return None
        codes.append(code)
    return tuple(codes)


class RequiresPermissions(BasePermission):
    """Require every live permission named by ``required_permissions``.

    Empty requirements fail closed. Single-permission views keep using
    ``RequiresPermission``.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        codes = _requirement_codes(getattr(view, "required_permissions", ()))
        granted: object = getattr(request, "permission_codes", frozenset())
        if codes is None or isinstance(granted, str) or not isinstance(granted, frozenset | set):
            return False
        return all(code in granted for code in codes)


class RequiresEntitlements(BasePermission):
    """Require every active feature named by ``required_features``.

    Empty requirements and a missing gym fail closed. Single-feature views keep
    using ``RequiresEntitlement``.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        codes = _requirement_codes(getattr(view, "required_features", ()))
        gym = getattr(request, "gym", None)
        if codes is None or gym is None:
            return False
        now = timezone.now()
        entitled = set(
            GymFeatureEntitlement.objects.filter(
                Q(effective_from__isnull=True) | Q(effective_from__lte=now),
                Q(effective_until__isnull=True) | Q(effective_until__gt=now),
                gym=gym,
                feature__code__in=codes,
                enabled=True,
            ).values_list("feature__code", flat=True)
        )
        return all(code in entitled for code in codes)


class TrainerFinanceClosed(BasePermission):
    """Close non-PT finance routes for a protected trainer-only membership.

    Place this after the ordinary permission and entitlement checks. A missing
    grant or feature stays 403. Protected OWNER or MANAGER composition, and a
    non-trainer custom role, continue through the granted permission. Phase 5
    PT sale, commission, package, and booking views do not use this class.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        actor = getattr(request, "gym_membership", None)
        gym = getattr(request, "gym", None)
        gym_id = getattr(gym, "id", None)
        if actor is None or not isinstance(gym_id, UUID):
            return False
        from apps.trainers.selectors import deny_trainer_only

        deny_trainer_only(gym_id=gym_id, actor=actor)
        return True


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

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.tenants.models import GymFeatureEntitlement


class RequiresPermission(BasePermission):
    required_permission = ""

    def has_permission(self, request: Request, view: APIView) -> bool:
        code = getattr(view, "required_permission", self.required_permission)
        return bool(code) and code in getattr(request, "permission_codes", frozenset())


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

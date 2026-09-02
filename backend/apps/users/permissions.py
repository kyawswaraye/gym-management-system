from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class RequiresPermission(BasePermission):
    required_permission = ""

    def has_permission(self, request: Request, view: APIView) -> bool:
        code = getattr(view, "required_permission", self.required_permission)
        return bool(code) and code in getattr(request, "permission_codes", frozenset())

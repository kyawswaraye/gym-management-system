from __future__ import annotations

import uuid
from datetime import datetime

from django.core import signing
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import PlatformAuditLog
from apps.audit.serializers import (
    PlatformAuditListEnvelopeSerializer,
    PlatformAuditQuerySerializer,
    TenantAuditListEnvelopeSerializer,
    TenantAuditQuerySerializer,
)
from apps.audit.services import list_tenant_audit_logs, redact_audit_values, tenant_audit_data
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership
from apps.users.authentication import PlatformJWTAuthentication, StaffJWTAuthentication
from apps.users.models import RefreshSession
from apps.users.permissions import RequiresPermission, RequiresPlatformPermissions


class PlatformAuditListView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions = ("platform.audit.view",)

    @extend_schema(
        operation_id="admin_audit_logs_list",
        parameters=[PlatformAuditQuerySerializer],
        responses={
            200: PlatformAuditListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = PlatformAuditQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        target_gym_id = values.get("target_gym_id")
        action = str(values.get("action", ""))
        outcome = str(values.get("outcome", ""))
        limit = int(values["limit"])

        queryset = PlatformAuditLog.objects.select_related("actor_user", "target_gym").order_by(
            "-occurred_at", "-id"
        )
        if target_gym_id:
            queryset = queryset.filter(target_gym_id=target_gym_id)
        if action:
            queryset = queryset.filter(action=action)
        if outcome:
            queryset = queryset.filter(outcome=outcome)
        if after := values.get("after"):
            try:
                cursor = signing.loads(
                    str(after),
                    salt="platform-audit-list",
                    max_age=24 * 60 * 60,
                )
                expected = {
                    "target_gym_id": str(target_gym_id or ""),
                    "action": action,
                    "outcome": outcome,
                }
                if any(cursor.get(key, "") != value for key, value in expected.items()):
                    raise signing.BadSignature
                occurred_at = datetime.fromisoformat(cursor["occurred_at"])
                audit_id = uuid.UUID(cursor["id"])
            except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
                raise serializers.ValidationError({"after": ["Invalid cursor."]}) from exc
            queryset = queryset.filter(
                Q(occurred_at__lt=occurred_at) | Q(occurred_at=occurred_at, id__lt=audit_id)
            )

        rows = list(queryset[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_after = None
        if has_more and rows:
            last = rows[-1]
            next_after = signing.dumps(
                {
                    "occurred_at": last.occurred_at.isoformat(),
                    "id": str(last.id),
                    "target_gym_id": str(target_gym_id or ""),
                    "action": action,
                    "outcome": outcome,
                },
                salt="platform-audit-list",
                compress=True,
            )

        data = [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "actor_email": row.actor_user.email,
                "target_gym_id": row.target_gym_id,
                "target_gym_code": row.target_gym.code if row.target_gym else None,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "action": row.action,
                "module": row.module,
                "outcome": row.outcome,
                "old_values": redact_audit_values(row.old_values),
                "new_values": redact_audit_values(row.new_values),
                "request_id": row.request_id,
                "occurred_at": row.occurred_at,
            }
            for row in rows
        ]
        return success_response(
            data,
            meta={"has_more": has_more, "next_after": next_after},
        )


def _tenant_audit_actor(request: Request) -> tuple[Gym, GymMembership]:
    gym = getattr(request, "gym", None)
    membership = getattr(request, "gym_membership", None)
    if (
        getattr(request, "principal_type", None) != RefreshSession.PrincipalType.STAFF
        or not isinstance(gym, Gym)
        or not isinstance(membership, GymMembership)
        or membership.gym_id != gym.id
        or membership.status != GymMembership.Status.ACTIVE
    ):
        raise AuthenticationFailed()
    return gym, membership


class TenantAuditListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "audit.view"

    @extend_schema(
        operation_id="audit_logs_list",
        parameters=[TenantAuditQuerySerializer],
        responses={
            200: TenantAuditListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym, membership = _tenant_audit_actor(request)
        query = TenantAuditQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        target_id = values.get("target_id")
        set_gym_context(gym_id=gym.id)
        rows, meta = list_tenant_audit_logs(
            gym_id=gym.id,
            membership_id=membership.id,
            is_owner=membership.is_owner,
            membership_status=membership.status,
            action=str(values.get("action", "")),
            module=str(values.get("module", "")),
            outcome=str(values.get("outcome", "")),
            target_type=str(values.get("target_type", "")),
            target_id=target_id if isinstance(target_id, uuid.UUID) else None,
            limit=int(values["limit"]),
            after=str(values.get("after", "")),
        )
        return success_response([tenant_audit_data(row) for row in rows], meta=meta)

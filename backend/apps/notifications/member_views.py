"""Member inbox routes. Staff and platform tokens are rejected."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.exceptions import RequestRejected
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.api.validation import validated
from apps.notifications.member_serializers import (
    EmptyCommandSerializer,
    MemberNotificationEnvelopeSerializer,
    MemberNotificationListEnvelopeSerializer,
    MemberNotificationQuerySerializer,
    MemberNotificationSerializer,
)
from apps.notifications.member_services import (
    mark_notification_read,
    notification_item,
    page_member_notifications,
)
from apps.users.authentication import MemberJWTAuthentication
from apps.users.permissions import RequiresEntitlements
from apps.users.throttling import MemberSelfReadThrottle, MemberSelfWriteThrottle

_IDEMPOTENCY_REJECTED = {"Idempotency-Key": ["Idempotency-Key is not accepted for this route."]}


def _context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "actor_user": request.user,
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", ""))[:512],
    }


class MemberNotificationView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresEntitlements]
    required_features = ("members",)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.method not in {"GET", "HEAD"} and request.headers.get("Idempotency-Key"):
            raise RequestRejected(_IDEMPOTENCY_REJECTED)


class MemberNotificationListView(MemberNotificationView):
    throttle_classes = [MemberSelfReadThrottle]

    @extend_schema(
        operation_id="member_notifications_list",
        parameters=[MemberNotificationQuerySerializer],
        responses={
            200: MemberNotificationListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = validated(MemberNotificationQuerySerializer(data=request.query_params))
        rows, meta = page_member_notifications(
            gym=request.gym,
            member_id=request.member.id,
            limit=int(query["limit"]),
            after=str(query.get("after") or ""),
        )
        return success_response(
            MemberNotificationSerializer([notification_item(row) for row in rows], many=True).data,
            meta=meta,
        )


class MemberNotificationReadView(MemberNotificationView):
    throttle_classes = [MemberSelfWriteThrottle]

    @extend_schema(
        operation_id="member_notifications_read",
        request=EmptyCommandSerializer,
        responses={
            200: MemberNotificationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        validated(EmptyCommandSerializer(data=request.data))
        row = mark_notification_read(
            gym=request.gym,
            member_id=request.member.id,
            notification_id=id,
            **_context(request),
        )
        return success_response(MemberNotificationSerializer(notification_item(row)).data)

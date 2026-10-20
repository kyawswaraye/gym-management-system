"""Staff announcement routes. Recipients are never accepted from the client."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.announcements.serializers import (
    AnnouncementEnvelopeSerializer,
    AnnouncementListEnvelopeSerializer,
    AnnouncementListQuerySerializer,
    AnnouncementPatchSerializer,
    AnnouncementSerializer,
    AnnouncementWriteSerializer,
    EmptyCommandSerializer,
)
from apps.announcements.services import (
    announcement_payload,
    cancel_announcement,
    create_announcement,
    get_announcement,
    page_announcements,
    publish_announcement,
    update_announcement,
)
from apps.common.api.exceptions import RequestRejected
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.api.validation import validated
from apps.files.throttling import MembershipWindowThrottle
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_REJECTED = {"Idempotency-Key": ["Idempotency-Key is not accepted for this route."]}


class AnnouncementCommandThrottle(MembershipWindowThrottle):
    scope = "announcement_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


def _audit_context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", ""))[:512],
        "actor_user": request.user,
    }


class AnnouncementAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "members"
    required_permission = "announcements.view"

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.method not in {"GET", "HEAD"} and request.headers.get("Idempotency-Key"):
            raise RequestRejected(_IDEMPOTENCY_REJECTED)


class AnnouncementListCreateView(AnnouncementAPIView):
    def get_permissions(self):
        self.required_permission = (
            "announcements.manage" if self.request.method == "POST" else "announcements.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [AnnouncementCommandThrottle()]
        return []

    @extend_schema(
        operation_id="announcements_list",
        parameters=[AnnouncementListQuerySerializer],
        responses={
            200: AnnouncementListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = validated(AnnouncementListQuerySerializer(data=request.query_params))
        rows, meta = page_announcements(
            gym=request.gym,
            status=str(query.get("status") or ""),
            announcement_type=str(query.get("type") or ""),
            audience=str(query.get("audience") or ""),
            limit=int(query["limit"]),
            after=str(query.get("after") or ""),
        )
        return success_response(
            AnnouncementSerializer([announcement_payload(row) for row in rows], many=True).data,
            meta=meta,
        )

    @extend_schema(
        operation_id="announcements_create",
        request=AnnouncementWriteSerializer,
        responses={
            201: AnnouncementEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        fields = validated(AnnouncementWriteSerializer(data=request.data))
        row = create_announcement(
            gym=request.gym,
            actor=request.gym_membership,
            fields=fields,
            **_audit_context(request),
        )
        return success_response(AnnouncementSerializer(announcement_payload(row)).data, status=201)


class AnnouncementDetailView(AnnouncementAPIView):
    def get_permissions(self):
        self.required_permission = (
            "announcements.manage" if self.request.method == "PATCH" else "announcements.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return [AnnouncementCommandThrottle()]
        return []

    @extend_schema(
        operation_id="announcements_read",
        responses={
            200: AnnouncementEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        row = get_announcement(gym=request.gym, announcement_id=id)
        return success_response(AnnouncementSerializer(announcement_payload(row)).data)

    @extend_schema(
        operation_id="announcements_update",
        request=AnnouncementPatchSerializer,
        responses={
            200: AnnouncementEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        fields = validated(AnnouncementPatchSerializer(data=request.data))
        row = update_announcement(
            gym=request.gym,
            actor=request.gym_membership,
            announcement_id=id,
            fields=fields,
            **_audit_context(request),
        )
        return success_response(AnnouncementSerializer(announcement_payload(row)).data)


class AnnouncementPublishView(AnnouncementAPIView):
    throttle_classes = [AnnouncementCommandThrottle]
    required_permission = "announcements.manage"

    @extend_schema(
        operation_id="announcements_publish",
        request=EmptyCommandSerializer,
        responses={
            200: AnnouncementEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        validated(EmptyCommandSerializer(data=request.data))
        row = publish_announcement(
            gym=request.gym,
            actor=request.gym_membership,
            announcement_id=id,
            **_audit_context(request),
        )
        return success_response(AnnouncementSerializer(announcement_payload(row)).data)


class AnnouncementCancelView(AnnouncementAPIView):
    throttle_classes = [AnnouncementCommandThrottle]
    required_permission = "announcements.manage"

    @extend_schema(
        operation_id="announcements_cancel",
        request=EmptyCommandSerializer,
        responses={
            200: AnnouncementEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        validated(EmptyCommandSerializer(data=request.data))
        row = cancel_announcement(
            gym=request.gym,
            actor=request.gym_membership,
            announcement_id=id,
            **_audit_context(request),
        )
        return success_response(AnnouncementSerializer(announcement_payload(row)).data)

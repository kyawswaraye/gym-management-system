"""Member device registration. Staff and platform tokens are rejected."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.exceptions import RequestRejected
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.api.validation import validated
from apps.members.member_device_serializers import (
    MemberDeviceEnvelopeSerializer,
    MemberDevicePatchSerializer,
    MemberDeviceSerializer,
    MemberDeviceWriteSerializer,
)
from apps.members.member_device_services import (
    delete_member_device,
    device_payload,
    register_member_device,
    update_member_device,
)
from apps.users.authentication import MemberJWTAuthentication
from apps.users.permissions import RequiresEntitlements
from apps.users.throttling import MemberSelfWriteThrottle

_IDEMPOTENCY_REJECTED = {"Idempotency-Key": ["Idempotency-Key is not accepted for this route."]}


def _context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "actor_user": request.user,
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", ""))[:512],
    }


class MemberDeviceView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresEntitlements]
    throttle_classes = [MemberSelfWriteThrottle]
    required_features = ("members",)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.headers.get("Idempotency-Key"):
            raise RequestRejected(_IDEMPOTENCY_REJECTED)


class MemberDeviceListCreateView(MemberDeviceView):
    @extend_schema(
        operation_id="member_devices_register",
        request=MemberDeviceWriteSerializer,
        responses={
            200: MemberDeviceEnvelopeSerializer,
            201: MemberDeviceEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        fields = validated(MemberDeviceWriteSerializer(data=request.data))
        device, created = register_member_device(
            gym=request.gym,
            member_id=request.member.id,
            platform=str(fields["platform"]),
            installation_id=fields["installation_id"],
            push_consent=bool(fields["push_consent"]),
            push_token=fields.get("push_token"),
            app_version=str(fields["app_version"]),
            os_version=str(fields["os_version"]),
            device_name=fields.get("device_name"),
            **_context(request),
        )
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return success_response(MemberDeviceSerializer(device_payload(device)).data, status=code)


class MemberDeviceDetailView(MemberDeviceView):
    @extend_schema(
        operation_id="member_devices_update",
        request=MemberDevicePatchSerializer,
        responses={
            200: MemberDeviceEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        changes = validated(MemberDevicePatchSerializer(data=request.data))
        device = update_member_device(
            gym=request.gym,
            member_id=request.member.id,
            device_id=id,
            changes=changes,
            **_context(request),
        )
        return success_response(MemberDeviceSerializer(device_payload(device)).data)

    @extend_schema(
        operation_id="member_devices_delete",
        responses={
            204: None,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def delete(self, request: Request, id: UUID) -> Response:
        delete_member_device(
            gym=request.gym,
            member_id=request.member.id,
            device_id=id,
            **_context(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

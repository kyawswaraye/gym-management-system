"""Staff display-device management and device-authenticated challenge issuance.

Management requires an active owner, both ``settings.manage`` and
``checkins.challenge.issue``, the members and memberships entitlements, and
recent MFA on enrollment, rotation, and revocation. ``settings.manage`` is
reserved to the owner role. The device route does not accept a staff token, a
member token, or the browser BFF secret.
"""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.members.device_serializers import (
    DeviceIngestEnvelopeSerializer,
    DeviceIngestResultSerializer,
    DeviceIngestSerializer,
    DisplayDeviceCredentialEnvelopeSerializer,
    DisplayDeviceCredentialSerializer,
    DisplayDeviceEmptySerializer,
    DisplayDeviceEnrollSerializer,
    DisplayDeviceEnvelopeSerializer,
    DisplayDeviceListEnvelopeSerializer,
    DisplayDeviceListQuerySerializer,
    DisplayDeviceSerializer,
    IntegrationDeviceEnrollSerializer,
)
from apps.members.device_services import (
    enroll_display_device,
    enroll_integration_device,
    issue_device_check_in_challenge,
    page_display_devices,
    page_integration_devices,
    revoke_display_device,
    revoke_integration_device,
    rotate_display_device_credential,
    rotate_integration_device_credential,
)
from apps.members.ingest_services import ingest_device_check_in
from apps.members.models import CheckInDevice
from apps.members.serializers import (
    CheckInChallengeEnvelopeSerializer,
    CheckInChallengeIssuedSerializer,
)
from apps.users.authentication import StaffJWTAuthentication
from apps.users.permissions import (
    IsActiveOwner,
    RequiresEntitlements,
    RequiresPermissions,
    RequiresRecentMFA,
)
from apps.users.throttling import (
    DeviceChallengeIPThrottle,
    DeviceChallengeSelectorThrottle,
    DeviceIngestIPThrottle,
    DeviceIngestSelectorThrottle,
)


def _request_id(request: Request) -> str:
    return str(getattr(request, "request_id", "") or "req_unknown")


def _client_address(request: Request) -> str | None:
    address = request.META.get("REMOTE_ADDR")
    return str(address) if address else None


def _user_agent(request: Request) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))


def _reject_idempotency(request: Request) -> None:
    if request.headers.get("Idempotency-Key"):
        raise ValidationError(
            {"Idempotency-Key": ["Idempotency-Key is not accepted for a display device."]}
        )


def _device_data(device: CheckInDevice, credential: str | None = None) -> dict[str, object]:
    data: dict[str, object] = {
        "id": device.id,
        "label": device.label,
        "location": device.location,
        "kind": device.kind,
        "status": device.status,
        "credential_selector": device.credential_selector,
        "credential_version": device.credential_version,
        "created_at": device.created_at,
        "rotated_at": device.rotated_at,
        "revoked_at": device.revoked_at,
    }
    if credential is not None:
        data["credential"] = credential
    return data


class _DeviceManagementView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        IsActiveOwner,
        RequiresPermissions,
        RequiresEntitlements,
    ]
    required_permissions = ("settings.manage", "checkins.challenge.issue")
    required_features = ("members", "memberships")


class DisplayDeviceListCreateView(_DeviceManagementView):
    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions.append(RequiresRecentMFA())
        return permissions

    @extend_schema(
        operation_id="check_in_display_devices_list",
        parameters=[
            OpenApiParameter(name="limit", type=int, location=OpenApiParameter.QUERY),
            OpenApiParameter(name="after", type=str, location=OpenApiParameter.QUERY),
        ],
        responses={
            200: DisplayDeviceListEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = DisplayDeviceListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        rows, meta = page_display_devices(
            gym=request.gym,
            actor=request.gym_membership,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(DisplayDeviceSerializer(rows, many=True).data, meta=meta)

    @extend_schema(
        operation_id="check_in_display_devices_create",
        request=DisplayDeviceEnrollSerializer,
        responses={
            201: DisplayDeviceCredentialEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        _reject_idempotency(request)
        serializer = DisplayDeviceEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrolled = enroll_display_device(
            gym=request.gym,
            actor=request.gym_membership,
            label=str(serializer.validated_data["label"]),
            location=str(serializer.validated_data["location"]),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            DisplayDeviceCredentialSerializer(
                _device_data(enrolled.device, enrolled.credential)
            ).data,
            status=status.HTTP_201_CREATED,
        )


class DisplayDeviceRotateView(_DeviceManagementView):
    permission_classes = [*_DeviceManagementView.permission_classes, RequiresRecentMFA]

    @extend_schema(
        operation_id="check_in_display_devices_rotate_credential",
        request=DisplayDeviceEmptySerializer,
        responses={
            200: DisplayDeviceCredentialEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        serializer = DisplayDeviceEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rotated = rotate_display_device_credential(
            gym=request.gym,
            actor=request.gym_membership,
            device_id=id,
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            DisplayDeviceCredentialSerializer(_device_data(rotated.device, rotated.credential)).data
        )


class DisplayDeviceRevokeView(_DeviceManagementView):
    permission_classes = [*_DeviceManagementView.permission_classes, RequiresRecentMFA]

    @extend_schema(
        operation_id="check_in_display_devices_revoke",
        request=DisplayDeviceEmptySerializer,
        responses={
            200: DisplayDeviceEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        serializer = DisplayDeviceEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = revoke_display_device(
            gym=request.gym,
            actor=request.gym_membership,
            device_id=id,
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(DisplayDeviceSerializer(_device_data(device)).data)


class DeviceCheckInChallengeView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [DeviceChallengeIPThrottle, DeviceChallengeSelectorThrottle]

    @extend_schema(
        operation_id="device_check_in_challenges_create",
        request=DisplayDeviceEmptySerializer,
        parameters=[
            OpenApiParameter(
                name="Authorization",
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "Device credential. The value is a public selector plus a secret. "
                    "This contract does not publish a sample secret."
                ),
            )
        ],
        responses={
            201: CheckInChallengeEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        if request.headers.get("Idempotency-Key"):
            raise ValidationError(
                {"Idempotency-Key": ["Idempotency-Key is not accepted for a display device."]}
            )
        serializer = DisplayDeviceEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issued = issue_device_check_in_challenge(
            authorization=str(request.headers.get("Authorization", "")),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        challenge = issued.challenge
        return success_response(
            CheckInChallengeIssuedSerializer(
                {
                    "id": challenge.id,
                    "payload": issued.payload,
                    "display_location": challenge.display_location,
                    "issued_at": challenge.issued_at,
                    "expires_at": challenge.expires_at,
                    "max_uses": challenge.max_uses,
                    "status": challenge.status,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class IntegrationDeviceListCreateView(_DeviceManagementView):
    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions.append(RequiresRecentMFA())
        return permissions

    @extend_schema(
        operation_id="check_in_integration_devices_list",
        parameters=[
            OpenApiParameter(name="limit", type=int, location=OpenApiParameter.QUERY),
            OpenApiParameter(name="after", type=str, location=OpenApiParameter.QUERY),
        ],
        responses={
            200: DisplayDeviceListEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = DisplayDeviceListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        rows, meta = page_integration_devices(
            gym=request.gym,
            actor=request.gym_membership,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(DisplayDeviceSerializer(rows, many=True).data, meta=meta)

    @extend_schema(
        operation_id="check_in_integration_devices_create",
        request=IntegrationDeviceEnrollSerializer,
        responses={
            201: DisplayDeviceCredentialEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        _reject_idempotency(request)
        serializer = IntegrationDeviceEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrolled = enroll_integration_device(
            gym=request.gym,
            actor=request.gym_membership,
            label=str(serializer.validated_data["label"]),
            location=str(serializer.validated_data["location"]),
            kind=str(serializer.validated_data["kind"]),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            DisplayDeviceCredentialSerializer(
                _device_data(enrolled.device, enrolled.credential)
            ).data,
            status=status.HTTP_201_CREATED,
        )


class IntegrationDeviceRotateView(_DeviceManagementView):
    permission_classes = [*_DeviceManagementView.permission_classes, RequiresRecentMFA]

    @extend_schema(
        operation_id="check_in_integration_devices_rotate_credential",
        request=DisplayDeviceEmptySerializer,
        responses={
            200: DisplayDeviceCredentialEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        serializer = DisplayDeviceEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rotated = rotate_integration_device_credential(
            gym=request.gym,
            actor=request.gym_membership,
            device_id=id,
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            DisplayDeviceCredentialSerializer(_device_data(rotated.device, rotated.credential)).data
        )


class IntegrationDeviceRevokeView(_DeviceManagementView):
    permission_classes = [*_DeviceManagementView.permission_classes, RequiresRecentMFA]

    @extend_schema(
        operation_id="check_in_integration_devices_revoke",
        request=DisplayDeviceEmptySerializer,
        responses={
            200: DisplayDeviceEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        serializer = DisplayDeviceEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = revoke_integration_device(
            gym=request.gym,
            actor=request.gym_membership,
            device_id=id,
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(DisplayDeviceSerializer(_device_data(device)).data)


class DeviceCheckInIngestView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [DeviceIngestIPThrottle, DeviceIngestSelectorThrottle]

    @extend_schema(
        operation_id="device_check_ins_create",
        request=DeviceIngestSerializer,
        parameters=[
            OpenApiParameter(
                name="Authorization",
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "NFC or biometric device credential. A display credential cannot "
                    "ingest. This contract does not publish a sample secret."
                ),
            )
        ],
        responses={
            200: DeviceIngestEnvelopeSerializer,
            201: DeviceIngestEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        if request.headers.get("Idempotency-Key"):
            raise ValidationError(
                {"Idempotency-Key": ["Idempotency-Key is not accepted for device ingestion."]}
            )
        serializer = DeviceIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        check_in, created = ingest_device_check_in(
            authorization=str(request.headers.get("Authorization", "")),
            member_id=serializer.validated_data["member_id"],
            event_id=str(serializer.validated_data["event_id"]),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            DeviceIngestResultSerializer(
                {
                    "id": check_in.id,
                    "member_id": check_in.member_id,
                    "method": check_in.method,
                    "check_in_at": check_in.check_in_at,
                    "status": "OPEN" if check_in.check_out_at is None else "CHECKED_OUT",
                }
            ).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.tenants.models import Gym, GymMembership, StaffProfile
from apps.tenants.staff_serializers import (
    StaffAcceptInputSerializer,
    StaffEnvelopeSerializer,
    StaffInvitationEnvelopeSerializer,
    StaffInviteInputSerializer,
    StaffLifecycleSerializer,
    StaffListEnvelopeSerializer,
    StaffListQuerySerializer,
    StaffSessionEnvelopeSerializer,
    StaffUpdateSerializer,
)
from apps.tenants.staff_services import (
    accept_staff_invitation,
    activate_staff,
    invite_staff,
    page_staff_profiles,
    staff_profile_data,
    staff_queryset,
    suspend_staff,
    update_staff_profile,
)
from apps.users.authentication import StaffJWTAuthentication
from apps.users.models import RefreshSession
from apps.users.permissions import RequiresPermission
from apps.users.services import TokenPair, create_staff_session

BFF_HEADER_PARAMETER = OpenApiParameter(
    name="X-Gym-BFF-Secret",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Shared secret presented only by the trusted browser BFF.",
)


class StaffObjectPermission(RequiresPermission):
    """Re-check the view permission and reject owner or cross-gym records."""

    def has_object_permission(self, request: Request, view: APIView, obj: StaffProfile) -> bool:
        gym = getattr(request, "gym", None)
        membership = obj.gym_membership
        return bool(
            isinstance(gym, Gym)
            and obj.gym_id == gym.id
            and membership.gym_id == gym.id
            and not membership.is_owner
            and self.has_permission(request, view)
        )


def _staff_actor(request: Request) -> tuple[Gym, GymMembership]:
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


def _request_context(request: Request) -> dict[str, Any]:
    return {
        "request_id": getattr(request, "request_id", "req_unknown"),
        "ip_address": request.META.get("REMOTE_ADDR"),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def _load_staff(gym: Gym, staff_id: uuid.UUID) -> StaffProfile:
    try:
        return staff_queryset(gym).get(id=staff_id)
    except StaffProfile.DoesNotExist as exc:
        raise Http404 from exc


class StaffListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "staff.view"

    @extend_schema(
        operation_id="staff_list",
        parameters=[StaffListQuerySerializer],
        responses={
            200: StaffListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym, _membership = _staff_actor(request)
        query = StaffListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        search = str(filters.get("search", ""))
        profile_status = str(filters.get("status", ""))
        membership_status = str(filters.get("membership_status", ""))
        queryset = staff_queryset(gym)
        if profile_status:
            queryset = queryset.filter(status=profile_status)
        if membership_status:
            queryset = queryset.filter(
                gym_membership__status=membership_status,
                gym_membership__gym=gym,
            )
        if search:
            queryset = queryset.filter(
                Q(display_name__icontains=search)
                | Q(staff_code__icontains=search)
                | Q(phone__icontains=search)
                | Q(gym_membership__user__email__icontains=search)
                | Q(gym_membership__user__first_name__icontains=search)
                | Q(gym_membership__user__last_name__icontains=search)
            )
        profiles, meta = page_staff_profiles(
            queryset,
            gym_id=gym.id,
            search=search,
            profile_status=profile_status,
            membership_status=membership_status,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(
            [staff_profile_data(profile) for profile in profiles],
            meta=meta,
        )


class StaffInvitationCreateView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, StaffObjectPermission]
    required_permission = "staff.manage"

    @extend_schema(
        operation_id="staff_invite",
        request=StaffInviteInputSerializer,
        responses={
            201: StaffInvitationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = StaffInviteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        payload = dict(serializer.validated_data)
        role_ids = list(payload.pop("role_ids"))
        invitation = invite_staff(
            gym_id=gym.id,
            actor=request.user,
            actor_membership_id=membership.id,
            staff_data=payload,
            role_ids=role_ids,
            **_request_context(request),
        )
        return success_response(
            invitation,
            message="Staff invitation created",
            status=status.HTTP_201_CREATED,
        )


class StaffInvitationAcceptView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="staff_invitation_accept",
        parameters=[BFF_HEADER_PARAMETER],
        request=StaffAcceptInputSerializer,
        responses={
            200: StaffSessionEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = StaffAcceptInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        token = str(payload.pop("invitation_token"))
        password = str(payload.pop("password"))
        payload.pop("password_confirmation", None)
        membership = accept_staff_invitation(
            plaintext_token=token,
            password=password,
            confirmation=payload,
            **_request_context(request),
        )
        pair: TokenPair = create_staff_session(
            user=membership.user,
            membership=membership,
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return success_response(
            {
                "access_token": pair.access_token,
                "access_expires_at": pair.access_expires_at,
                "refresh_token": pair.refresh_token,
                "refresh_expires_at": pair.refresh_expires_at,
                "requires_gym_selection": False,
            }
        )


class StaffDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, StaffObjectPermission]
    required_permission = "staff.view"

    def get_permissions(self) -> list[BasePermission]:
        self.required_permission = (
            "staff.manage" if self.request.method == "PATCH" else "staff.view"
        )
        return [permission() for permission in self.permission_classes]

    @extend_schema(
        operation_id="staff_retrieve",
        responses={
            200: StaffEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: uuid.UUID) -> Response:
        gym, _membership = _staff_actor(request)
        profile = _load_staff(gym, id)
        self.check_object_permissions(request, profile)
        return success_response(staff_profile_data(profile))

    @extend_schema(
        operation_id="staff_update",
        request=StaffUpdateSerializer,
        responses={
            200: StaffEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: uuid.UUID) -> Response:
        gym, membership = _staff_actor(request)
        profile = _load_staff(gym, id)
        self.check_object_permissions(request, profile)
        serializer = StaffUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_staff_profile(
                gym_id=gym.id,
                staff_id=profile.id,
                actor=request.user,
                actor_membership_id=membership.id,
                changes=dict(serializer.validated_data),
                **_request_context(request),
            )
        except StaffProfile.DoesNotExist as exc:
            raise Http404 from exc
        return success_response(staff_profile_data(updated), message="Staff profile updated")


class StaffLifecycleView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, StaffObjectPermission]
    required_permission = "staff.manage"
    command: Any = None
    success_message = "Staff updated"

    def post(self, request: Request, id: uuid.UUID) -> Response:
        gym, membership = _staff_actor(request)
        profile = _load_staff(gym, id)
        self.check_object_permissions(request, profile)
        serializer = StaffLifecycleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = self.command(
                gym_id=gym.id,
                staff_id=profile.id,
                actor=request.user,
                actor_membership_id=membership.id,
                reason=str(serializer.validated_data["reason"]),
                **_request_context(request),
            )
        except StaffProfile.DoesNotExist as exc:
            raise Http404 from exc
        return success_response(staff_profile_data(updated), message=self.success_message)


class StaffActivateView(StaffLifecycleView):
    command = staticmethod(activate_staff)
    success_message = "Staff activated"

    @extend_schema(
        operation_id="staff_activate",
        request=StaffLifecycleSerializer,
        responses={
            200: StaffEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: uuid.UUID) -> Response:
        return super().post(request, id)


class StaffSuspendView(StaffLifecycleView):
    command = staticmethod(suspend_staff)
    success_message = "Staff suspended"

    @extend_schema(
        operation_id="staff_suspend",
        request=StaffLifecycleSerializer,
        responses={
            200: StaffEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: uuid.UUID) -> Response:
        return super().post(request, id)

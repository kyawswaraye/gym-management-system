from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from django.core import signing
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.tenants.logo import clear_gym_logo, read_logo_upload, replace_gym_logo
from apps.tenants.models import Gym, GymLogo, GymMembership, GymSettings, SubscriptionPlan
from apps.tenants.serializers import (
    SUBSCRIPTION_LIST_STATUS_ALL,
    AdminDashboardEnvelopeSerializer,
    AdminDashboardSerializer,
    AdminGymDetailEnvelopeSerializer,
    AdminGymLifecycleInputSerializer,
    AdminGymListEnvelopeSerializer,
    AdminGymListQuerySerializer,
    AdminGymPatchSerializer,
    AdminGymProvisionEnvelopeSerializer,
    AdminGymProvisionRequestSerializer,
    AdminSubscriptionDetailEnvelopeSerializer,
    AdminSubscriptionDetailSerializer,
    AdminSubscriptionListEnvelopeSerializer,
    AdminSubscriptionListQuerySerializer,
    AdminSubscriptionSerializer,
    AdminSubscriptionUpdateSerializer,
    GymProfileEnvelopeSerializer,
    GymProfileSerializer,
    GymProfileUpdateSerializer,
    GymSettingsEnvelopeSerializer,
    GymSettingsSerializer,
    GymSettingsUpdateSerializer,
    OwnerAcceptInputSerializer,
    OwnerInvitationEnvelopeSerializer,
    OwnerInviteInputSerializer,
    OwnerItemEnvelopeSerializer,
    OwnerListEnvelopeSerializer,
    OwnerRevokeInputSerializer,
)
from apps.tenants.services import (
    OWNER_INVITATION_PENDING_MESSAGE,
    accept_owner_invitation,
    admin_gym_detail,
    cancel_owner_invitation,
    canonical_provisioning_hash,
    invite_owner,
    owner_invitation_data,
    owner_membership_data,
    platform_dashboard_counts,
    provision_gym_from_admin,
    revoke_owner,
    transition_gym_from_admin,
    update_gym_from_admin,
    update_gym_profile,
    update_gym_settings,
    update_subscription_plan_from_admin,
    visible_owner_memberships,
)
from apps.users.authentication import PlatformJWTAuthentication, StaffJWTAuthentication
from apps.users.models import RefreshSession
from apps.users.permissions import (
    IsActiveOwner,
    RequiresPermission,
    RequiresPlatformPermissions,
    RequiresRecentMFA,
)
from apps.users.services import create_staff_session

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of the same provisioning request.",
)


class AdminGymListCreateView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions: tuple[str, ...] = ("platform.gyms.view",)

    def get_permissions(self) -> Sequence[Any]:
        self.required_permissions = (
            ("platform.gyms.create", "platform.owners.create")
            if self.request.method == "POST"
            else ("platform.gyms.view",)
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="admin_gyms_list",
        parameters=[
            AdminGymListQuerySerializer,
        ],
        responses={
            200: AdminGymListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = AdminGymListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        search = str(values.get("search", "")).strip()
        gym_status = values.get("status")
        limit = int(values["limit"])

        queryset = Gym.objects.select_related("subscription_plan").order_by("-created_at", "-id")
        if search:
            queryset = queryset.filter(
                Q(code__icontains=search)
                | Q(name__icontains=search)
                | Q(legal_name__icontains=search)
            )
        if gym_status:
            queryset = queryset.filter(status=gym_status)
        if after := values.get("after"):
            try:
                cursor = signing.loads(
                    str(after),
                    salt="admin-gym-list",
                    max_age=24 * 60 * 60,
                )
                if cursor.get("search") != search or cursor.get("status") != gym_status:
                    raise signing.BadSignature
                created_at = datetime.fromisoformat(cursor["created_at"])
                gym_id = uuid.UUID(cursor["id"])
            except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
                raise serializers.ValidationError({"after": ["Invalid cursor."]}) from exc
            queryset = queryset.filter(
                Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=gym_id)
            )

        rows = list(queryset[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_after = None
        if has_more and rows:
            last = rows[-1]
            next_after = signing.dumps(
                {
                    "created_at": last.created_at.isoformat(),
                    "id": str(last.id),
                    "search": search,
                    "status": gym_status,
                },
                salt="admin-gym-list",
                compress=True,
            )
        data = [
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "status": row.status,
                "timezone": row.timezone,
                "currency": row.currency,
                "subscription_plan_id": row.subscription_plan_id,
                "subscription_plan_name": (
                    row.subscription_plan.name if row.subscription_plan else None
                ),
                "subscription_status": row.subscription_status,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        return success_response(
            data,
            meta={"has_more": has_more, "next_after": next_after},
        )

    @extend_schema(
        operation_id="admin_gyms_create",
        request=AdminGymProvisionRequestSerializer,
        parameters=[IDEMPOTENCY_HEADER],
        responses={
            201: AdminGymProvisionEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise serializers.ValidationError(
                {"Idempotency-Key": ["Use 16 to 128 URL-safe high-entropy characters."]}
            )
        serializer = AdminGymProvisionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        request_hash = canonical_provisioning_hash(validated)
        result = provision_gym_from_admin(
            actor=request.user,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            gym_data=dict(validated["gym"]),
            owner_data=dict(validated["owner"]),
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = success_response(
            result.data,
            message="Gym provisioned",
            status=status.HTTP_201_CREATED,
        )
        response["Idempotency-Replayed"] = "true" if result.replayed else "false"
        return response


class AdminDashboardView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions = ("platform.dashboard.view",)

    @extend_schema(
        operation_id="admin_dashboard_retrieve",
        responses={
            200: AdminDashboardEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        payload = AdminDashboardSerializer(platform_dashboard_counts())
        return success_response(payload.data)


class AdminSubscriptionListView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions = ("platform.subscriptions.manage",)

    @extend_schema(
        operation_id="admin_subscriptions_list",
        parameters=[AdminSubscriptionListQuerySerializer],
        responses={
            200: AdminSubscriptionListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = AdminSubscriptionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        selected = str(query.validated_data["status"])
        plans = SubscriptionPlan.objects.all()
        if selected != SUBSCRIPTION_LIST_STATUS_ALL:
            plans = plans.filter(status=selected)
        plans = plans.order_by("name", "id")
        return success_response(AdminSubscriptionSerializer(plans, many=True).data)


class AdminSubscriptionDetailView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions = ("platform.subscriptions.manage",)

    @extend_schema(
        operation_id="admin_subscriptions_update",
        request=AdminSubscriptionUpdateSerializer,
        responses={
            200: AdminSubscriptionDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: uuid.UUID) -> Response:
        serializer = AdminSubscriptionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan, changed = update_subscription_plan_from_admin(
                plan_id=id,
                data=dict(serializer.validated_data),
                actor=request.user,
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except SubscriptionPlan.DoesNotExist:
            raise Http404 from None
        message = "Subscription plan updated" if changed else "Subscription plan unchanged"
        return success_response(AdminSubscriptionDetailSerializer(plan).data, message=message)


class AdminGymDetailView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    required_permissions: tuple[str, ...] = ("platform.gyms.view",)

    def get_permissions(self) -> Sequence[Any]:
        if self.request.method == "GET":
            self.required_permissions = ("platform.gyms.view",)
        else:
            subscription_fields = {
                "subscription_plan_id",
                "subscription_status",
                "subscription_starts_at",
                "subscription_ends_at",
            }
            submitted = set(self.request.data)
            required = []
            if submitted - subscription_fields:
                required.append("platform.gyms.update")
            if submitted & subscription_fields:
                required.append("platform.subscriptions.manage")
            self.required_permissions = tuple(required or ["platform.gyms.update"])
        return super().get_permissions()

    @extend_schema(
        operation_id="admin_gym_detail",
        responses={
            200: AdminGymDetailEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, gym_id: uuid.UUID) -> Response:
        gym = get_object_or_404(
            Gym.objects.select_related("subscription_plan"),
            id=gym_id,
        )
        return success_response(admin_gym_detail(gym))

    @extend_schema(
        operation_id="admin_gym_update",
        request=AdminGymPatchSerializer,
        responses={
            200: AdminGymDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, gym_id: uuid.UUID) -> Response:
        serializer = AdminGymPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            gym = update_gym_from_admin(
                gym_id=gym_id,
                data=dict(serializer.validated_data),
                actor=request.user,
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except Gym.DoesNotExist:
            raise Http404 from None
        return success_response(admin_gym_detail(gym), message="Gym updated")


class AdminGymLifecycleView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPlatformPermissions]
    target_status = ""
    action_name = ""
    required_permissions: tuple[str, ...] = ()

    def post(self, request: Request, gym_id: uuid.UUID) -> Response:
        serializer = AdminGymLifecycleInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            gym, changed = transition_gym_from_admin(
                gym_id=gym_id,
                target_status=self.target_status,
                reason=str(serializer.validated_data["reason"]),
                actor=request.user,
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except Gym.DoesNotExist:
            raise Http404 from None
        message = f"Gym {self.action_name}" if changed else f"Gym already {self.action_name}"
        return success_response(admin_gym_detail(gym), message=message)


class AdminGymActivateView(AdminGymLifecycleView):
    target_status = Gym.Status.ACTIVE
    action_name = "active"
    required_permissions = ("platform.gyms.activate",)

    @extend_schema(
        operation_id="admin_gym_activate",
        request=AdminGymLifecycleInputSerializer,
        responses={
            200: AdminGymDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, gym_id: uuid.UUID) -> Response:
        return super().post(request, gym_id)


class AdminGymSuspendView(AdminGymLifecycleView):
    target_status = Gym.Status.SUSPENDED
    action_name = "suspended"
    required_permissions = ("platform.gyms.suspend",)

    @extend_schema(
        operation_id="admin_gym_suspend",
        request=AdminGymLifecycleInputSerializer,
        responses={
            200: AdminGymDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, gym_id: uuid.UUID) -> Response:
        return super().post(request, gym_id)


class OwnerListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, IsActiveOwner]

    @extend_schema(
        operation_id="owners_list",
        responses={
            200: OwnerListEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym = request.gym
        owners = visible_owner_memberships(gym)
        return success_response([owner_membership_data(owner) for owner in owners])


class OwnerInvitationCreateView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, IsActiveOwner, RequiresRecentMFA]

    @extend_schema(
        operation_id="owners_invite",
        request=OwnerInviteInputSerializer,
        responses={
            201: OwnerInvitationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = OwnerInviteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym = request.gym
        membership = request.gym_membership
        invitation, created = invite_owner(
            gym_id=gym.id,
            actor=request.user,
            actor_membership_id=membership.id,
            owner_data=dict(serializer.validated_data),
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return success_response(
            owner_invitation_data(invitation),
            message="Owner invitation created" if created else OWNER_INVITATION_PENDING_MESSAGE,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class OwnerInvitationAcceptView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="owners_invitation_accept",
        request=OwnerAcceptInputSerializer,
        responses={
            200: OpenApiTypes.OBJECT,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = OwnerAcceptInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = accept_owner_invitation(
            plaintext_token=str(serializer.validated_data["invitation_token"]),
            password=str(serializer.validated_data["password"]),
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        pair = create_staff_session(user=membership.user, membership=membership)
        return success_response(
            {
                "access_token": pair.access_token,
                "access_expires_at": pair.access_expires_at,
                "refresh_token": pair.refresh_token,
                "refresh_expires_at": pair.refresh_expires_at,
                "requires_gym_selection": False,
            }
        )


class OwnerRevokeView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, IsActiveOwner, RequiresRecentMFA]

    @extend_schema(
        operation_id="owners_revoke",
        request=OwnerRevokeInputSerializer,
        responses={
            200: OwnerItemEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, gym_membership_id: uuid.UUID) -> Response:
        serializer = OwnerRevokeInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym = request.gym
        membership = request.gym_membership
        try:
            owner = revoke_owner(
                gym_id=gym.id,
                target_membership_id=gym_membership_id,
                actor=request.user,
                actor_membership_id=membership.id,
                reason=str(serializer.validated_data["reason"]),
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except GymMembership.DoesNotExist:
            raise Http404 from None
        return success_response(owner_membership_data(owner), message="Owner revoked")


class OwnerInvitationCancelView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, IsActiveOwner, RequiresRecentMFA]

    @extend_schema(
        operation_id="owners_invitation_cancel",
        request=None,
        responses={
            200: OwnerItemEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, gym_membership_id: uuid.UUID) -> Response:
        gym = request.gym
        membership = request.gym_membership
        try:
            owner = cancel_owner_invitation(
                gym_id=gym.id,
                target_membership_id=gym_membership_id,
                actor=request.user,
                actor_membership_id=membership.id,
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except GymMembership.DoesNotExist:
            raise Http404 from None
        return success_response(owner_membership_data(owner), message="Owner invitation removed")


class AdminGymOwnerCreateView(APIView):
    authentication_classes = [PlatformJWTAuthentication]
    required_permissions: tuple[str, ...] = ("platform.owners.create",)

    def get_permissions(self) -> Sequence[Any]:
        if self.request.method == "GET":
            self.required_permissions = ("platform.gyms.view",)
            return [IsAuthenticated(), RequiresPlatformPermissions()]
        self.required_permissions = ("platform.owners.create",)
        return [
            IsAuthenticated(),
            RequiresPlatformPermissions(),
            RequiresRecentMFA(),
        ]

    @extend_schema(
        operation_id="admin_gym_owners_list",
        responses={
            200: OwnerListEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, gym_id: uuid.UUID) -> Response:
        gym = get_object_or_404(Gym, id=gym_id)
        owners = visible_owner_memberships(gym)
        return success_response([owner_membership_data(owner) for owner in owners])

    @extend_schema(
        operation_id="admin_gym_owner_invite",
        request=OwnerInviteInputSerializer,
        responses={
            201: OwnerInvitationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, gym_id: uuid.UUID) -> Response:
        serializer = OwnerInviteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invitation, created = invite_owner(
                gym_id=gym_id,
                actor=request.user,
                actor_membership_id=None,
                owner_data=dict(serializer.validated_data),
                request_id=getattr(request, "request_id", "req_unknown"),
                platform_assisted=True,
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except Gym.DoesNotExist:
            raise Http404 from None
        return success_response(
            owner_invitation_data(invitation),
            message="Owner invitation created" if created else OWNER_INVITATION_PENDING_MESSAGE,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _tenant_actor(request: Request) -> tuple[Gym, GymMembership]:
    gym = getattr(request, "gym", None)
    membership = getattr(request, "gym_membership", None)
    if (
        getattr(request, "principal_type", None) != RefreshSession.PrincipalType.STAFF
        or not isinstance(gym, Gym)
        or not isinstance(membership, GymMembership)
        or membership.gym_id != gym.id
    ):
        raise AuthenticationFailed()
    return gym, membership


class GymProfileView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "gym.view"

    def get_permissions(self) -> Sequence[Any]:
        self.required_permission = "gym.update" if self.request.method == "PATCH" else "gym.view"
        return super().get_permissions()

    @extend_schema(
        operation_id="gym_profile_retrieve",
        responses={
            200: GymProfileEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym, _membership = _tenant_actor(request)
        return success_response(GymProfileSerializer(gym).data)

    @extend_schema(
        operation_id="gym_profile_update",
        request=GymProfileUpdateSerializer,
        responses={
            200: GymProfileEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request) -> Response:
        gym, membership = _tenant_actor(request)
        serializer = GymProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = update_gym_profile(
            gym_id=gym.id,
            data=dict(serializer.validated_data),
            actor=request.user,
            actor_membership_id=membership.id,
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return success_response(GymProfileSerializer(updated).data, message="Gym profile updated")


class GymLogoView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "gym.view"

    def get_permissions(self) -> Sequence[Any]:
        self.required_permission = "gym.view" if self.request.method == "GET" else "gym.update"
        return super().get_permissions()

    @extend_schema(
        operation_id="gym_logo_retrieve",
        responses={
            200: OpenApiTypes.BINARY,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> HttpResponse:
        gym, _membership = _tenant_actor(request)
        logo = GymLogo.objects.filter(pk=gym.id).first()
        if logo is None or logo.content_type not in (
            "image/jpeg",
            "image/png",
            "image/webp",
        ):
            raise Http404
        response = HttpResponse(bytes(logo.data), content_type=logo.content_type)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @extend_schema(
        operation_id="gym_logo_update",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {"logo": {"type": "string", "format": "binary"}},
                "required": ["logo"],
            }
        },
        responses={
            200: GymProfileEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def put(self, request: Request) -> Response:
        upload = request.FILES.get("logo")
        if upload is None:
            raise serializers.ValidationError({"logo": ["Choose a logo image."]})
        data, content_type = read_logo_upload(upload)
        gym, membership = _tenant_actor(request)
        replace_gym_logo(
            gym_id=gym.id,
            data=data,
            content_type=content_type,
            actor=request.user,
            actor_membership_id=membership.id,
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return success_response(GymProfileSerializer(gym).data, message="Gym logo updated")

    @extend_schema(
        operation_id="gym_logo_destroy",
        responses={
            200: GymProfileEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def delete(self, request: Request) -> Response:
        gym, membership = _tenant_actor(request)
        clear_gym_logo(
            gym_id=gym.id,
            actor=request.user,
            actor_membership_id=membership.id,
            request_id=getattr(request, "request_id", "req_unknown"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return success_response(GymProfileSerializer(gym).data, message="Gym logo removed")


class GymSettingsView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "settings.view"

    def get_permissions(self) -> Sequence[Any]:
        if self.request.method == "PATCH":
            self.required_permission = "settings.manage"
            permissions: list[type] = [IsAuthenticated, RequiresPermission, IsActiveOwner]
        else:
            self.required_permission = "settings.view"
            permissions = [IsAuthenticated, RequiresPermission]
        return [permission() for permission in permissions]

    def _settings(self, gym: Gym) -> GymSettings:
        try:
            return GymSettings.objects.get(gym=gym)
        except GymSettings.DoesNotExist as exc:
            raise Http404 from exc

    @extend_schema(
        operation_id="gym_settings_retrieve",
        responses={
            200: GymSettingsEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym, _membership = _tenant_actor(request)
        return success_response(GymSettingsSerializer(self._settings(gym)).data)

    @extend_schema(
        operation_id="gym_settings_update",
        request=GymSettingsUpdateSerializer,
        responses={
            200: GymSettingsEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request) -> Response:
        gym, membership = _tenant_actor(request)
        serializer = GymSettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_gym_settings(
                gym_id=gym.id,
                data=dict(serializer.validated_data),
                actor=request.user,
                actor_membership_id=membership.id,
                request_id=getattr(request, "request_id", "req_unknown"),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except GymSettings.DoesNotExist as exc:
            raise Http404 from exc
        return success_response(GymSettingsSerializer(updated).data, message="Settings updated")

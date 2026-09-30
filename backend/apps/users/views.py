from __future__ import annotations

import uuid
from typing import Any

from django.db import connection, transaction
from django.db.models import Q
from django.db.transaction import non_atomic_requests
from django.utils import timezone
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.common.api.exceptions import AuthenticationFailed, CodedAPIException
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context, set_identity_context
from apps.tenants.models import GymFeatureEntitlement, GymMembership

from .authentication import StaffJWTAuthentication
from .models import RefreshSession, User
from .schema import (
    CurrentUserEnvelopeSerializer,
    DirectLoginEnvelopeSerializer,
    GymMembershipListEnvelopeSerializer,
    SelectionLoginEnvelopeSerializer,
    TokenEnvelopeSerializer,
)
from .serializers import (
    LoginInputSerializer,
    LogoutInputSerializer,
    RefreshInputSerializer,
    SelectGymInputSerializer,
)
from .services import (
    TokenPair,
    consume_gym_selection_ticket,
    create_staff_session,
    issue_gym_selection_ticket,
    revoke_all_user_sessions,
    revoke_refresh_token,
    rotate_refresh_token,
)
from .throttling import GymSelectionIssueThrottle, GymSelectionThrottle

BFF_HEADER_PARAMETER = OpenApiParameter(
    name="X-Gym-BFF-Secret",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Shared secret presented only by the trusted browser BFF.",
)


class ProviderNotConfigured(CodedAPIException):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    default_code = "PROVIDER_NOT_CONFIGURED"
    default_detail = "This provider-backed operation is not configured."


def _membership_rows(user_id: uuid.UUID) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT gym_membership_id, gym_id, gym_code, gym_name, membership_status, "
            "is_owner, authz_version FROM app_staff_memberships()"
        )
        return [
            {
                "gym_membership_id": str(row[0]),
                "gym_id": str(row[1]),
                "gym": {"code": row[2], "name": row[3]},
                "status": row[4],
                "is_owner": row[5],
                "authz_version": row[6],
            }
            for row in cursor.fetchall()
        ]


def _safe_membership_choices(memberships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "gym_membership_id": item["gym_membership_id"],
            "gym_id": item["gym_id"],
            "gym": item["gym"],
        }
        for item in memberships
    ]


def _token_data(pair: TokenPair) -> dict[str, Any]:
    return {
        "access_token": pair.access_token,
        "access_expires_at": pair.access_expires_at,
        "refresh_token": pair.refresh_token,
        "refresh_expires_at": pair.refresh_expires_at,
    }


@method_decorator(non_atomic_requests, name="dispatch")
class LoginView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, GymSelectionIssueThrottle]

    @extend_schema(
        operation_id="auth_login",
        request=LoginInputSerializer,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            200: PolymorphicProxySerializer(
                component_name="LoginEnvelope",
                serializers=[DirectLoginEnvelopeSerializer, SelectionLoginEnvelopeSerializer],
                resource_type_field_name=None,
            ),
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
            501: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        # Request-level ATOMIC_REQUESTS is disabled so ticket/refresh denials can
        # persist. Tenant GUCs still require an explicit transaction.
        with transaction.atomic():
            serializer = LoginInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            email = str(serializer.validated_data["email"]).strip().lower()
            try:
                user = User.objects.get(normalized_email=email, is_active=True)
            except User.DoesNotExist as exc:
                User().set_password(str(serializer.validated_data["password"]))
                raise AuthenticationFailed() from exc
            if not user.check_password(str(serializer.validated_data["password"])):
                raise AuthenticationFailed()
            if user.mfa_enabled:
                raise ProviderNotConfigured()
            set_identity_context(user_id=user.id, request_id=request.request_id)
            memberships = _membership_rows(user.id)
            if not memberships:
                raise AuthenticationFailed()
            if len(memberships) != 1:
                ticket, expires_at = issue_gym_selection_ticket(
                    user=user,
                    memberships=memberships,
                    request_id=request.request_id,
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
                return success_response(
                    {
                        "requires_gym_selection": True,
                        "gym_selection_ticket": ticket,
                        "gym_selection_expires_at": expires_at,
                        "available_gym_memberships": _safe_membership_choices(memberships),
                    }
                )
            selected = memberships[0]
            set_gym_context(gym_id=uuid.UUID(selected["gym_id"]))
            membership = GymMembership.objects.get(
                id=selected["gym_membership_id"],
                user=user,
                status=GymMembership.Status.ACTIVE,
            )
            pair = create_staff_session(
                user=user,
                membership=membership,
                device_name=str(serializer.validated_data.get("device_name", "")),
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            return success_response(
                {
                    **_token_data(pair),
                    "requires_gym_selection": False,
                    "available_gym_memberships": _safe_membership_choices(memberships),
                }
            )


@method_decorator(non_atomic_requests, name="dispatch")
class RefreshView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_refresh",
        request=RefreshInputSerializer,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            200: TokenEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pair = rotate_refresh_token(
            str(serializer.validated_data["refresh_token"]), request_id=request.request_id
        )
        return success_response(_token_data(pair))


@method_decorator(non_atomic_requests, name="dispatch")
class LogoutView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_logout",
        request=LogoutInputSerializer,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            204: OpenApiResponse(response=None, description="Session revoked."),
            400: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revoke_refresh_token(str(serializer.validated_data["refresh_token"]))
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    @extend_schema(
        operation_id="auth_logout_all",
        request=None,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            204: OpenApiResponse(response=None, description="All user sessions revoked."),
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        revoke_all_user_sessions(request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class GymsView(APIView):
    @extend_schema(
        operation_id="auth_gyms",
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            200: GymMembershipListEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        return success_response(_membership_rows(request.user.id))


@method_decorator(non_atomic_requests, name="dispatch")
class SelectGymView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, GymSelectionThrottle]

    @extend_schema(
        operation_id="auth_select_gym",
        request=SelectGymInputSerializer,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            200: TokenEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
        description=(
            "Consumes a protected gym-selection ticket, or switches gyms when a staff "
            "Bearer token is supplied by the trusted BFF."
        ),
    )
    def post(self, request: Request) -> Response:
        serializer = SelectGymInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested = serializer.validated_data["gym_membership_id"]
        ticket = serializer.validated_data.get("gym_selection_ticket")
        if ticket:
            pair = consume_gym_selection_ticket(
                str(ticket),
                gym_membership_id=requested,
                request_id=request.request_id,
                membership_loader=_membership_rows,
            )
            return success_response(_token_data(pair))
        with transaction.atomic():
            authenticated = StaffJWTAuthentication().authenticate(request)
            if authenticated is None:
                raise AuthenticationFailed()
            request.user, request.auth = authenticated
            if not getattr(request, "user", None) or not getattr(
                request.user, "is_authenticated", False
            ):
                raise AuthenticationFailed()
            row = next(
                (
                    item
                    for item in _membership_rows(request.user.id)
                    if item["gym_membership_id"] == str(requested)
                ),
                None,
            )
            if row is None:
                raise AuthenticationFailed()
            set_gym_context(gym_id=uuid.UUID(row["gym_id"]))
            membership = GymMembership.objects.get(
                id=requested,
                gym_id=row["gym_id"],
                user=request.user,
                status=GymMembership.Status.ACTIVE,
            )
            RefreshSession.objects.filter(
                id=request.refresh_session.id, revoked_at__isnull=True
            ).update(revoked_at=timezone.now(), revocation_reason="GYM_SELECTION_REPLACED")
            pair = create_staff_session(user=request.user, membership=membership)
            return success_response(_token_data(pair))


class MeView(APIView):
    @extend_schema(
        operation_id="auth_me",
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            200: CurrentUserEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        membership = request.gym_membership
        now = timezone.now()
        roles = list(
            membership.gymmembershiprole_set.filter(gym=request.gym, role__is_active=True)
            .values_list("role__key", flat=True)
            .order_by("role__key")
        )
        entitlements = list(
            GymFeatureEntitlement.objects.filter(
                Q(effective_from__isnull=True) | Q(effective_from__lte=now),
                Q(effective_until__isnull=True) | Q(effective_until__gt=now),
                gym=request.gym,
                enabled=True,
            )
            .values("feature__code", "limit_value")
            .order_by("feature__code")
        )
        return success_response(
            {
                "user": {
                    "id": str(request.user.id),
                    "email": request.user.email,
                    "first_name": request.user.first_name,
                    "last_name": request.user.last_name,
                },
                "gym": {
                    "id": str(request.gym.id),
                    "code": request.gym.code,
                    "name": request.gym.name,
                    "currency": request.gym.currency,
                    "timezone": request.gym.timezone,
                },
                "gym_membership": {
                    "id": str(membership.id),
                    "status": membership.status,
                    "is_owner": membership.is_owner,
                    "roles": roles,
                },
                "permissions": sorted(request.permission_codes),
                "features": entitlements,
                "authz_version": membership.authz_version,
            }
        )


class ProviderNeutralUnavailableView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        parameters=[BFF_HEADER_PARAMETER],
        responses={
            403: ErrorEnvelopeSerializer,
            501: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        raise ProviderNotConfigured()

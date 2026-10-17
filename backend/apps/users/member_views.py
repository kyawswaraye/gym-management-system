from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.transaction import non_atomic_requests
from django.utils.decorators import method_decorator
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import Outcome
from apps.audit.services import write_platform_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.tenants.models import Gym

from .member_sessions import login_member, rotate_member_refresh_token
from .models import User
from .schema import MemberTokenEnvelopeSerializer
from .serializers import LogoutInputSerializer, MemberLoginInputSerializer, RefreshInputSerializer
from .services import TokenPair, revoke_refresh_token
from .throttling import (
    MemberLoginIPThrottle,
    MemberLoginThrottle,
    MemberLogoutIPThrottle,
    MemberLogoutTokenThrottle,
    MemberRefreshIPThrottle,
    MemberRefreshTokenThrottle,
)


def _token_data(pair: TokenPair, *, member_id: str, gym_id: str) -> dict[str, Any]:
    return {
        "access_token": pair.access_token,
        "access_expires_at": pair.access_expires_at,
        "refresh_token": pair.refresh_token,
        "refresh_expires_at": pair.refresh_expires_at,
        "member_id": member_id,
        "gym_id": gym_id,
    }


def _client_ip(request: Request) -> str | None:
    remote = request.META.get("REMOTE_ADDR")
    if not isinstance(remote, str) or not remote:
        return None
    return remote


@method_decorator(non_atomic_requests, name="dispatch")
class MemberLoginView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [MemberLoginIPThrottle, MemberLoginThrottle]

    @extend_schema(
        operation_id="member_auth_login",
        request=MemberLoginInputSerializer,
        responses={
            200: MemberTokenEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = MemberLoginInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            pair, member = login_member(
                email=str(serializer.validated_data["email"]),
                password=str(serializer.validated_data["password"]),
                device_name=str(serializer.validated_data.get("device_name", "")),
                ip_address=_client_ip(request),
                request_id=getattr(request, "request_id", "req_unknown"),
            )
            linked_user_id = member.linked_user_id
            if linked_user_id is None:
                raise AuthenticationFailed()
            write_platform_audit(
                actor=User.objects.get(pk=linked_user_id),
                action="member_auth.login",
                target_type="Member",
                target_id=member.id,
                target_gym=Gym.objects.get(pk=member.gym_id),
                request_id=getattr(request, "request_id", "req_unknown"),
                outcome=Outcome.SUCCESS,
                details={"gym_id": str(member.gym_id)},
                ip_address=_client_ip(request),
                user_agent=str(request.META.get("HTTP_USER_AGENT", "")),
            )
        return success_response(
            _token_data(pair, member_id=str(member.id), gym_id=str(member.gym_id))
        )


@method_decorator(non_atomic_requests, name="dispatch")
class MemberRefreshView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [MemberRefreshIPThrottle, MemberRefreshTokenThrottle]

    @extend_schema(
        operation_id="member_auth_refresh",
        request=RefreshInputSerializer,
        responses={
            200: MemberTokenEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pair, member = rotate_member_refresh_token(
            str(serializer.validated_data["refresh_token"]),
            request_id=getattr(request, "request_id", "req_unknown"),
        )
        return success_response(
            _token_data(pair, member_id=str(member.id), gym_id=str(member.gym_id))
        )


@method_decorator(non_atomic_requests, name="dispatch")
class MemberLogoutView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [MemberLogoutIPThrottle, MemberLogoutTokenThrottle]

    @extend_schema(
        operation_id="member_auth_logout",
        request=LogoutInputSerializer,
        responses={
            204: OpenApiResponse(response=None, description="Member session revoked."),
            400: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revoke_refresh_token(str(serializer.validated_data["refresh_token"]))
        return Response(status=status.HTTP_204_NO_CONTENT)

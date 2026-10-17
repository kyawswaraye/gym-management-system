"""Member-self QR scan. Staff and platform tokens are not accepted."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.members.scan_services import redeem_member_check_in_challenge
from apps.members.serializers import (
    CheckInEnvelopeSerializer,
    CheckInSerializer,
    MemberCheckInScanSerializer,
)
from apps.users.authentication import MemberJWTAuthentication
from apps.users.throttling import MemberCheckInScanThrottle


def _request_id(request: Request) -> str:
    return str(getattr(request, "request_id", "") or "req_unknown")


def _client_address(request: Request) -> str | None:
    address = request.META.get("REMOTE_ADDR")
    return str(address) if address else None


class MemberCheckInScanView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [MemberCheckInScanThrottle]

    @extend_schema(
        operation_id="member_check_in_scan",
        request=MemberCheckInScanSerializer,
        responses={
            201: CheckInEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        if request.headers.get("Idempotency-Key"):
            raise ValidationError(
                {"Idempotency-Key": ["Idempotency-Key is not accepted for a member check-in scan."]}
            )
        serializer = MemberCheckInScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        check_in = redeem_member_check_in_challenge(
            gym=request.gym,
            member_id=request.member.id,
            user_id=request.user.pk,
            payload=str(serializer.validated_data["payload"]),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "")),
        )
        return success_response(
            CheckInSerializer(check_in).data,
            status=status.HTTP_201_CREATED,
        )

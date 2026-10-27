"""Member-self HTTP routes. Staff and platform tokens are rejected."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.classes.booking_services import create_member_class_booking
from apps.classes.views import IDEMPOTENCY_HEADER, _stored_response, require_idempotency_key
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.finance.idempotency import IdempotentResponse, run_idempotent
from apps.members.self_serializers import (
    MemberClassBookingEnvelopeSerializer,
    MemberClassBookingWriteSerializer,
    MemberScheduleBookingSerializer,
    MemberScheduleEnvelopeSerializer,
    MemberScheduleQuerySerializer,
    MemberScheduleSerializer,
    MemberSelfCheckInEnvelopeSerializer,
    MemberSelfDashboardEnvelopeSerializer,
    MemberSelfDashboardSerializer,
    MemberSelfEmptyQuerySerializer,
    MemberSelfEnvelopeSerializer,
    MemberSelfMembershipEnvelopeSerializer,
    MemberSelfMembershipSerializer,
    MemberSelfPackageEnvelopeSerializer,
    MemberSelfPackageQuerySerializer,
    MemberSelfPackageSerializer,
    MemberSelfPageQuerySerializer,
    MemberSelfPaymentEnvelopeSerializer,
    MemberSelfProfileSerializer,
    MemberSelfUpdateSerializer,
)
from apps.members.self_services import (
    load_self_member,
    member_class_booking_item,
    member_self_check_ins,
    member_self_dashboard,
    member_self_membership,
    member_self_packages,
    member_self_payments,
    member_self_schedule,
    update_self_profile,
)
from apps.members.serializers import CheckInSerializer, MemberPaymentHistoryItemSerializer
from apps.tenants.logo import logo_bytes_are_safe
from apps.tenants.models import GymLogo
from apps.users.authentication import MemberJWTAuthentication
from apps.users.permissions import RequiresEntitlements
from apps.users.throttling import MemberSelfReadThrottle, MemberSelfWriteThrottle

_IDEMPOTENCY_REJECTED = {
    "Idempotency-Key": ["Idempotency-Key is not accepted for this member route."]
}


def _request_id(request: Request) -> str:
    return str(getattr(request, "request_id", "") or "req_unknown")


def _client_address(request: Request) -> str | None:
    address = request.META.get("REMOTE_ADDR")
    return str(address) if address else None


def _user_agent(request: Request) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))[:512]


class MemberSelfView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresEntitlements]
    throttle_classes = [MemberSelfReadThrottle]
    required_features = ("members",)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.headers.get("Idempotency-Key"):
            raise ValidationError(_IDEMPOTENCY_REJECTED)


class MemberMeView(MemberSelfView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return [MemberSelfWriteThrottle()]
        return [MemberSelfReadThrottle()]

    @extend_schema(
        operation_id="member_me_read",
        parameters=[],
        responses={
            200: MemberSelfEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        MemberSelfEmptyQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        return success_response(MemberSelfProfileSerializer(member).data)

    @extend_schema(
        operation_id="member_me_update",
        parameters=[],
        request=MemberSelfUpdateSerializer,
        responses={
            200: MemberSelfEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request) -> Response:
        MemberSelfEmptyQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        serializer = MemberSelfUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = update_self_profile(
            gym=request.gym,
            member_id=request.member.id,
            actor_user=request.user,
            changes=dict(serializer.validated_data),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(MemberSelfProfileSerializer(member).data)


class MemberDashboardView(MemberSelfView):
    @extend_schema(
        operation_id="member_dashboard",
        parameters=[],
        responses={
            200: MemberSelfDashboardEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        MemberSelfEmptyQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        payload = member_self_dashboard(gym=request.gym, member=member)
        return success_response(MemberSelfDashboardSerializer(payload).data)


class MemberLogoRenderer(BaseRenderer):
    """Accept image requests. The view returns the raw logo bytes itself."""

    media_type = "image/*"
    format = "bin"
    charset = None
    render_style = "binary"

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        rendered = JSONRenderer().render(data, "application/json", renderer_context)
        if isinstance(rendered, (bytes, bytearray)):
            return bytes(rendered)
        raise TypeError("The logo error response was not bytes.")


class MemberGymLogoView(MemberSelfView):
    """Logo bytes for the signed-in member's gym. Another gym's logo is unreachable."""

    renderer_classes = [MemberLogoRenderer, JSONRenderer]

    @extend_schema(
        operation_id="member_gym_logo_retrieve",
        tags=["member"],
        parameters=[],
        responses={
            200: OpenApiTypes.BINARY,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> HttpResponse:
        MemberSelfEmptyQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        logo = GymLogo.objects.filter(pk=request.gym.id).first()
        if logo is None or not logo_bytes_are_safe(logo.content_type, bytes(logo.data)):
            raise Http404
        response = HttpResponse(bytes(logo.data), content_type=logo.content_type)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MemberMembershipView(MemberSelfView):
    required_features = ("members", "memberships")

    @extend_schema(
        operation_id="member_membership",
        parameters=[],
        responses={
            200: MemberSelfMembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        MemberSelfEmptyQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        current = member_self_membership(gym=request.gym, member=member)
        data = None if current is None else MemberSelfMembershipSerializer(current).data
        return success_response(data)


class MemberPaymentsView(MemberSelfView):
    required_features = ("members", "finance")

    @extend_schema(
        operation_id="member_payments",
        parameters=[MemberSelfPageQuerySerializer],
        responses={
            200: MemberSelfPaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberSelfPageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        rows, meta = member_self_payments(
            gym=request.gym,
            member=member,
            limit=int(query.validated_data["limit"]),
            after=str(query.validated_data.get("after", "")),
        )
        return success_response(
            MemberPaymentHistoryItemSerializer(rows, many=True).data,
            meta=meta,
        )


class MemberCheckInsView(MemberSelfView):
    required_features = ("members", "memberships")

    @extend_schema(
        operation_id="member_check_ins",
        parameters=[MemberSelfPageQuerySerializer],
        responses={
            200: MemberSelfCheckInEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberSelfPageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        rows, meta = member_self_check_ins(
            gym=request.gym,
            member=member,
            limit=int(query.validated_data["limit"]),
            after=str(query.validated_data.get("after", "")),
        )
        return success_response(CheckInSerializer(rows, many=True).data, meta=meta)


class MemberServicePackagesView(MemberSelfView):
    required_features = ("pt",)

    @extend_schema(
        operation_id="member_service_packages",
        parameters=[MemberSelfPackageQuerySerializer],
        responses={
            200: MemberSelfPackageEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberSelfPackageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        rows, meta = member_self_packages(
            gym=request.gym,
            member=member,
            status=str(payload.get("status", "")),
            package_type=str(payload.get("package_type", "")),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response(MemberSelfPackageSerializer(rows, many=True).data, meta=meta)


class MemberScheduleView(MemberSelfView):
    required_features = ("pt",)

    @extend_schema(
        operation_id="member_schedule",
        parameters=[MemberScheduleQuerySerializer],
        responses={
            200: MemberScheduleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberScheduleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        member = load_self_member(gym=request.gym, member_id=request.member.id)
        payload = member_self_schedule(
            gym=request.gym,
            member=member,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
        )
        return success_response(MemberScheduleSerializer(payload).data)


class MemberClassBookingView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresEntitlements]
    throttle_classes = [MemberSelfWriteThrottle]
    required_features = ("pt",)

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers
        try:
            with transaction.atomic():
                self.initial(request, *args, **kwargs)
            if request.method.lower() in self.http_method_names:
                handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed
            response = handler(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)
        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response

    @extend_schema(
        operation_id="member_class_bookings_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=MemberClassBookingWriteSerializer,
        responses={
            201: MemberClassBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        if request.query_params:
            raise ValidationError(
                {name: ["This field is not allowed."] for name in sorted(set(request.query_params))}
            )
        serializer = MemberClassBookingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        payload = serializer.validated_data
        member_id = request.member.id

        def mutate() -> IdempotentResponse:
            row = create_member_class_booking(
                gym=request.gym,
                member_id=member_id,
                actor_user=request.user,
                class_occurrence_id=payload["occurrence_id"],
                member_service_package_id=payload["member_service_package_id"],
                request_id=_request_id(request),
                ip_address=_client_address(request),
                user_agent=_user_agent(request),
            )
            rendered = {
                "success": True,
                "data": MemberScheduleBookingSerializer(member_class_booking_item(row)).data,
                "message": "Class booking created",
            }
            return IdempotentResponse(
                status=status.HTTP_201_CREATED,
                body=JSONRenderer().render(rendered),
                resource_type="ClassBooking",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=None,
            actor_member_id=member_id,
            http_method="POST",
            route="/api/v1/member/class-bookings/",
            idempotency_key=key,
            body=dict(payload),
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

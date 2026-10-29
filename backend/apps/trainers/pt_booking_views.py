"""Published personal-training booking routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.classes.booking_views import BookingCommandThrottle, EmptyCommandSerializer
from apps.classes.views import (
    IDEMPOTENCY_HEADER,
    _audit_context,
    _stored_response,
    require_idempotency_key,
)
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, run_idempotent
from apps.trainers.pt_booking_services import (
    attend_pt_booking,
    cancel_pt_booking,
    create_pt_booking,
    no_show_pt_booking,
)
from apps.trainers.selectors import get_pt_booking, page_pt_bookings
from apps.trainers.serializers import (
    PTBookingCancelSerializer,
    PTBookingEnvelopeSerializer,
    PTBookingListEnvelopeSerializer,
    PTBookingListQuerySerializer,
    PTBookingWriteSerializer,
    pt_booking_payload,
    pt_booking_payloads,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission


class PTBookingAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "pt.schedule.view"


class PTBookingCommandView(PTBookingAPIView):
    throttle_classes = [BookingCommandThrottle]
    required_permission = "pt.schedule.manage"

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


class PTBookingListCreateView(PTBookingCommandView):
    def get_permissions(self):
        self.required_permission = (
            "pt.schedule.manage" if self.request.method == "POST" else "pt.schedule.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [BookingCommandThrottle()]
        return []

    @extend_schema(
        operation_id="pt_bookings_list",
        parameters=[PTBookingListQuerySerializer],
        responses={200: PTBookingListEnvelopeSerializer, 400: ErrorEnvelopeSerializer},
    )
    def get(self, request: Request) -> Response:
        query = PTBookingListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_pt_bookings(
                gym=request.gym,
                actor=request.gym_membership,
                status=str(payload.get("status", "")),
                trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
                member_id=payload.get("member_id"),
                date_from=payload.get("date_from"),
                date_to=payload.get("date_to"),
                view=str(payload.get("view", "list")),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
            data = pt_booking_payloads(rows)
        return success_response(data, meta=meta)

    @extend_schema(
        operation_id="pt_bookings_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTBookingWriteSerializer,
        responses={
            201: PTBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = PTBookingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        payload = serializer.validated_data
        context = _audit_context(request)

        def mutate() -> IdempotentResponse:
            row = create_pt_booking(
                gym=request.gym,
                actor=request.gym_membership,
                trainer_staff_profile_id=payload["trainer_staff_profile_id"],
                member_id=payload["member_id"],
                starts_at=payload["starts_at"],
                ends_at=payload["ends_at"],
                member_service_package_id=payload.get("member_service_package_id"),
                unmetered=bool(payload.get("unmetered", False)),
                unmetered_reason=str(payload.get("unmetered_reason", "")),
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_booking_payload(row),
                "message": "Personal training booking created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="PTBooking",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route="/api/v1/pt-bookings/",
            idempotency_key=key,
            body=dict(payload),
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTBookingDetailView(PTBookingCommandView):
    required_permission = "pt.schedule.view"
    throttle_classes = []

    def get_throttles(self) -> list[Any]:
        return []

    @extend_schema(
        operation_id="pt_bookings_retrieve",
        responses={200: PTBookingEnvelopeSerializer, 404: ErrorEnvelopeSerializer},
    )
    def get(self, request: Request, id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            row = get_pt_booking(gym=request.gym, actor=request.gym_membership, booking_id=id)
            data = pt_booking_payload(row)
        return success_response(data)


class PTBookingAttendView(PTBookingCommandView):
    @extend_schema(
        operation_id="pt_bookings_attend",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: PTBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return _pt_action(request, booking_id=id, action="attend")


class PTBookingNoShowView(PTBookingCommandView):
    @extend_schema(
        operation_id="pt_bookings_no_show",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: PTBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return _pt_action(request, booking_id=id, action="no-show")


class PTBookingCancelView(PTBookingCommandView):
    @extend_schema(
        operation_id="pt_bookings_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTBookingCancelSerializer,
        responses={
            200: PTBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return _pt_action(request, booking_id=id, action="cancel")


def _pt_action(request: Request, *, booking_id: UUID, action: str) -> HttpResponse:
    if action == "cancel":
        serializer = PTBookingCancelSerializer(data=request.data)
    else:
        serializer = EmptyCommandSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    payload = serializer.validated_data
    context = _audit_context(request)

    def mutate() -> IdempotentResponse:
        if action == "attend":
            row = attend_pt_booking(
                gym=request.gym,
                actor=request.gym_membership,
                booking_id=booking_id,
                **context,
            )
            message = "Personal training booking attended"
        elif action == "no-show":
            row = no_show_pt_booking(
                gym=request.gym,
                actor=request.gym_membership,
                booking_id=booking_id,
                **context,
            )
            message = "Personal training booking marked no-show"
        else:
            row = cancel_pt_booking(
                gym=request.gym,
                actor=request.gym_membership,
                booking_id=booking_id,
                reason=str(payload.get("reason", "")),
                **context,
            )
            message = "Personal training booking cancelled"
        rendered = {"success": True, "data": pt_booking_payload(row), "message": message}
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="PTBooking",
            resource_id=row.id,
        )

    result = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=f"/api/v1/pt-bookings/{booking_id}/{action}/",
        idempotency_key=key,
        body=dict(payload),
        mutate=mutate,
        request=request,
    )
    return _stored_response(result)

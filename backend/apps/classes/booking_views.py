"""Published class booking routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.classes.booking_services import (
    attend_class_booking,
    cancel_class_booking,
    create_class_booking,
    no_show_class_booking,
)
from apps.classes.selectors import page_class_bookings
from apps.classes.serializers import (
    ClassBookingCancelSerializer,
    ClassBookingEnvelopeSerializer,
    ClassBookingListEnvelopeSerializer,
    ClassBookingListQuerySerializer,
    ClassBookingWriteSerializer,
    class_booking_payload,
    class_booking_payloads,
)
from apps.classes.views import (
    IDEMPOTENCY_HEADER,
    ClassCommandView,
    _audit_context,
    _stored_response,
    require_idempotency_key,
)
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, run_idempotent
from apps.files.throttling import MembershipWindowThrottle
from apps.users.permissions import RequiresEntitlement
from apps.users.serializers import StrictSerializer


class BookingCommandThrottle(MembershipWindowThrottle):
    scope = "booking_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


class BookOrManagePermission(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        granted: frozenset[str] = getattr(request, "permission_codes", frozenset())
        return "classes.book" in granted or "classes.manage" in granted


class EmptyCommandSerializer(StrictSerializer):
    """Attend and no-show commands carry no body."""


class ClassBookingListCreateView(ClassCommandView):
    throttle_classes = [BookingCommandThrottle]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), BookOrManagePermission(), RequiresEntitlement()]
        self.required_permission = "classes.view"
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [BookingCommandThrottle()]
        return []

    @extend_schema(
        operation_id="class_bookings_list",
        parameters=[ClassBookingListQuerySerializer],
        responses={200: ClassBookingListEnvelopeSerializer, 400: ErrorEnvelopeSerializer},
    )
    def get(self, request: Request) -> Response:
        query = ClassBookingListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_class_bookings(
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
            data = class_booking_payloads(rows)
        return success_response(data, meta=meta)

    @extend_schema(
        operation_id="class_bookings_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ClassBookingWriteSerializer,
        responses={
            201: ClassBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ClassBookingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        payload = serializer.validated_data
        context = _audit_context(request)

        def mutate() -> IdempotentResponse:
            row = create_class_booking(
                gym=request.gym,
                actor=request.gym_membership,
                class_occurrence_id=payload["class_occurrence_id"],
                member_id=payload["member_id"],
                member_service_package_id=payload.get("member_service_package_id"),
                unmetered=bool(payload.get("unmetered", False)),
                unmetered_reason=str(payload.get("unmetered_reason", "")),
                **context,
            )
            rendered = {
                "success": True,
                "data": class_booking_payload(row),
                "message": "Class booking created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="ClassBooking",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route="/api/v1/class-bookings/",
            idempotency_key=key,
            body=dict(payload),
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ClassBookingActionView(ClassCommandView):
    throttle_classes = [BookingCommandThrottle]
    action_name = ""

    def get_permissions(self):
        if self.action_name == "cancel":
            return [IsAuthenticated(), BookOrManagePermission(), RequiresEntitlement()]
        self.required_permission = "classes.attendance"
        return super().get_permissions()

    def post(self, request: Request, id: UUID) -> HttpResponse:
        if self.action_name == "cancel":
            serializer = ClassBookingCancelSerializer(data=request.data)
        else:
            serializer = EmptyCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        payload = serializer.validated_data
        context = _audit_context(request)
        route = f"/api/v1/class-bookings/{id}/{self.action_name}/"

        def mutate() -> IdempotentResponse:
            if self.action_name == "attend":
                row = attend_class_booking(
                    gym=request.gym,
                    actor=request.gym_membership,
                    booking_id=id,
                    **context,
                )
                message = "Class booking attended"
            elif self.action_name == "no-show":
                row = no_show_class_booking(
                    gym=request.gym,
                    actor=request.gym_membership,
                    booking_id=id,
                    **context,
                )
                message = "Class booking marked no-show"
            else:
                row = cancel_class_booking(
                    gym=request.gym,
                    actor=request.gym_membership,
                    booking_id=id,
                    reason=str(payload.get("reason", "")),
                    **context,
                )
                message = "Class booking cancelled"
            rendered = {"success": True, "data": class_booking_payload(row), "message": message}
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="ClassBooking",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=route,
            idempotency_key=key,
            body=dict(payload),
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ClassBookingAttendView(ClassBookingActionView):
    action_name = "attend"

    @extend_schema(
        operation_id="class_bookings_attend",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: ClassBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return super().post(request, id)


class ClassBookingNoShowView(ClassBookingActionView):
    action_name = "no-show"

    @extend_schema(
        operation_id="class_bookings_no_show",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: ClassBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return super().post(request, id)


class ClassBookingCancelView(ClassBookingActionView):
    action_name = "cancel"

    @extend_schema(
        operation_id="class_bookings_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=ClassBookingCancelSerializer,
        responses={
            200: ClassBookingEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        return super().post(request, id)

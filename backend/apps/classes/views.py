"""Published class type, schedule, and occurrence routes."""

from __future__ import annotations

import re
from datetime import time
from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.classes.selectors import (
    page_class_occurrences,
    page_class_schedules,
    page_class_types,
)
from apps.classes.serializers import (
    ClassOccurrenceCancelSerializer,
    ClassOccurrenceEnvelopeSerializer,
    ClassOccurrenceListEnvelopeSerializer,
    ClassOccurrenceListQuerySerializer,
    ClassScheduleEnvelopeSerializer,
    ClassScheduleListEnvelopeSerializer,
    ClassScheduleListQuerySerializer,
    ClassSchedulePatchSerializer,
    ClassScheduleWriteSerializer,
    ClassTypeEnvelopeSerializer,
    ClassTypeListEnvelopeSerializer,
    ClassTypeListQuerySerializer,
    ClassTypePatchSerializer,
    ClassTypeWriteSerializer,
    class_occurrence_payload,
    class_schedule_payload,
    class_type_payload,
)
from apps.classes.services import (
    cancel_class_occurrence,
    create_class_schedule,
    create_class_type,
    update_class_schedule,
    update_class_type,
)
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, IdempotentResult, run_idempotent
from apps.files.throttling import MembershipWindowThrottle
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this class command.",
)


class ClassCommandThrottle(MembershipWindowThrottle):
    scope = "class_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


def require_idempotency_key(header: str | None) -> str:
    if header is None or _IDEMPOTENCY_KEY.fullmatch(header) is None:
        raise ValidationError({"Idempotency-Key": ["Enter a valid idempotency key."]})
    return header


def schedule_route(schedule_id: UUID | None) -> str:
    if schedule_id is None:
        return "/api/v1/class-schedules/"
    return f"/api/v1/class-schedules/{schedule_id}/"


def schedule_command_body(payload: dict[str, Any]) -> dict[str, object]:
    """Normalize a validated schedule command for a stable request hash.

    Weekdays are stored sorted. Local times use ``isoformat`` so ``23:00`` and
    ``23:00:00`` hash as the same wall time. UUIDs and dates stay as objects
    because the idempotency hasher canonicalizes those types.
    """

    body: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, time):
            body[key] = value.isoformat()
        elif key == "weekdays":
            body[key] = sorted(int(item) for item in value)
        else:
            body[key] = value
    return body


def _stored_response(result: IdempotentResult) -> HttpResponse:
    return HttpResponse(result.body, status=result.status, content_type="application/json")


def _audit_context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", "")),
    }


class ClassAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "classes.view"


class ClassCommandView(ClassAPIView):
    throttle_classes = [ClassCommandThrottle]
    required_permission = "classes.manage"

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


class ClassTypeListCreateView(ClassAPIView):
    def get_permissions(self):
        self.required_permission = (
            "classes.manage" if self.request.method == "POST" else "classes.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [ClassCommandThrottle()]
        return []

    @extend_schema(
        operation_id="class_types_list",
        parameters=[ClassTypeListQuerySerializer],
        responses={
            200: ClassTypeListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ClassTypeListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        is_active = payload["is_active"] if "is_active" in payload else None
        rows, meta = page_class_types(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            is_active=is_active,
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([class_type_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="class_types_create",
        request=ClassTypeWriteSerializer,
        responses={
            201: ClassTypeEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ClassTypeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        row = create_class_type(
            gym=request.gym,
            actor=request.gym_membership,
            code=str(payload["code"]),
            name=str(payload["name"]),
            description=str(payload.get("description", "")),
            default_duration_minutes=int(payload["default_duration_minutes"]),
            default_capacity=int(payload["default_capacity"]),
            **_audit_context(request),
        )
        return success_response(
            class_type_payload(row),
            status=201,
            message="Class type created",
        )


class ClassTypeDetailView(ClassAPIView):
    throttle_classes = [ClassCommandThrottle]
    required_permission = "classes.manage"

    @extend_schema(
        operation_id="class_types_update",
        request=ClassTypePatchSerializer,
        responses={
            200: ClassTypeEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        serializer = ClassTypePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = update_class_type(
            gym=request.gym,
            actor=request.gym_membership,
            class_type_id=id,
            changes=dict(serializer.validated_data),
            **_audit_context(request),
        )
        return success_response(class_type_payload(row), message="Class type updated")


class ClassScheduleListCreateView(ClassCommandView):
    def get_permissions(self):
        self.required_permission = (
            "classes.manage" if self.request.method == "POST" else "classes.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    @extend_schema(
        operation_id="class_schedules_list",
        parameters=[ClassScheduleListQuerySerializer],
        responses={
            200: ClassScheduleListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ClassScheduleListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_class_schedules(
                gym_id=request.gym.id,
                actor=request.gym_membership,
                status=str(payload.get("status") or ""),
                trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
                class_type_id=payload.get("class_type_id"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([class_schedule_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="class_schedules_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ClassScheduleWriteSerializer,
        responses={
            201: ClassScheduleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ClassScheduleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        payload = serializer.validated_data
        body = schedule_command_body(dict(payload))

        def mutate() -> IdempotentResponse:
            row = create_class_schedule(
                gym=request.gym,
                actor=request.gym_membership,
                class_type_id=payload["class_type_id"],
                trainer_staff_profile_id=payload["trainer_staff_profile_id"],
                weekdays=list(payload["weekdays"]),
                local_start_time=payload["local_start_time"],
                local_end_time=payload["local_end_time"],
                effective_from=payload["effective_from"],
                effective_until=payload["effective_until"],
                location=str(payload["location"]),
                location_is_exclusive=bool(payload["location_is_exclusive"]),
                capacity=int(payload["capacity"]),
                **context,
            )
            rendered = {
                "success": True,
                "data": class_schedule_payload(row),
                "message": "Class schedule created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="ClassSchedule",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=schedule_route(None),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ClassScheduleDetailView(ClassCommandView):
    @extend_schema(
        operation_id="class_schedules_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=ClassSchedulePatchSerializer,
        responses={
            200: ClassScheduleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> HttpResponse:
        serializer = ClassSchedulePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        payload = serializer.validated_data
        body = schedule_command_body(dict(payload))

        def mutate() -> IdempotentResponse:
            row = update_class_schedule(
                gym=request.gym,
                actor=request.gym_membership,
                schedule_id=id,
                changes=dict(payload),
                **context,
            )
            rendered = {
                "success": True,
                "data": class_schedule_payload(row),
                "message": "Class schedule updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="ClassSchedule",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=schedule_route(id),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ClassOccurrenceListView(ClassAPIView):
    @extend_schema(
        operation_id="class_occurrences_list",
        parameters=[ClassOccurrenceListQuerySerializer],
        responses={
            200: ClassOccurrenceListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ClassOccurrenceListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_class_occurrences(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            status=str(payload.get("status") or ""),
            trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
            date_from=payload.get("date_from"),
            date_to=payload.get("date_to"),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([class_occurrence_payload(row) for row in rows], meta=meta)


class ClassOccurrenceCancelView(ClassCommandView):
    @extend_schema(
        operation_id="class_occurrences_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=ClassOccurrenceCancelSerializer,
        responses={
            200: ClassOccurrenceEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        serializer = ClassOccurrenceCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        reason = str(serializer.validated_data["reason"])

        def mutate() -> IdempotentResponse:
            set_gym_context(gym_id=request.gym.id)
            row = cancel_class_occurrence(
                gym=request.gym,
                actor=request.gym_membership,
                occurrence_id=id,
                reason=reason,
                **context,
            )
            rendered = {
                "success": True,
                "data": class_occurrence_payload(row),
                "message": "Class occurrence cancelled",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="ClassOccurrence",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=f"/api/v1/class-occurrences/{id}/cancel/",
            idempotency_key=key,
            body={"reason": reason},
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

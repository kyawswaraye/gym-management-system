"""Published trainer directory and assignment lifecycle routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.files.throttling import MembershipWindowThrottle
from apps.members.services import gym_today
from apps.trainers.selectors import (
    get_visible_assignment,
    page_assigned_members,
    page_assignments,
    page_service_packages,
    page_trainers,
)
from apps.trainers.serializers import (
    AssignedMemberListEnvelopeSerializer,
    AssignmentCancelSerializer,
    AssignmentCreateSerializer,
    AssignmentEndSerializer,
    AssignmentListQuerySerializer,
    PageQuerySerializer,
    ServicePackageEnvelopeSerializer,
    ServicePackageListEnvelopeSerializer,
    ServicePackageListQuerySerializer,
    ServicePackageWriteSerializer,
    TrainerAssignmentEnvelopeSerializer,
    TrainerAssignmentListEnvelopeSerializer,
    TrainerListEnvelopeSerializer,
    assigned_member_payload,
    assignment_payload,
    service_package_payload,
    trainer_payload,
)
from apps.trainers.services import (
    cancel_assignment,
    create_assignment,
    create_service_package,
    end_assignment,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission


class TrainerAssignmentCommandThrottle(MembershipWindowThrottle):
    scope = "trainer_assignment_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


class ServicePackageCommandThrottle(MembershipWindowThrottle):
    scope = "service_package_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


def _audit_context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", "")),
    }


class TrainerAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "pt.assignments.view"


class TrainerCommandView(TrainerAPIView):
    throttle_classes = [TrainerAssignmentCommandThrottle]
    required_permission = "pt.assignments.manage"


class TrainerListView(TrainerAPIView):
    @extend_schema(
        operation_id="trainers_list",
        parameters=[PageQuerySerializer],
        responses={
            200: TrainerListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = PageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_trainers(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([trainer_payload(row) for row in rows], meta=meta)


class TrainerAssignedMembersView(TrainerAPIView):
    @extend_schema(
        operation_id="trainers_assigned_members",
        parameters=[PageQuerySerializer],
        responses={
            200: AssignedMemberListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        query = PageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_assigned_members(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            trainer_id=id,
            as_of=gym_today(request.gym),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([assigned_member_payload(row) for row in rows], meta=meta)


class TrainerAssignmentListCreateView(TrainerAPIView):
    def get_permissions(self):
        self.required_permission = (
            "pt.assignments.manage" if self.request.method == "POST" else "pt.assignments.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [TrainerAssignmentCommandThrottle()]
        return []

    @extend_schema(
        operation_id="trainer_assignments_list",
        parameters=[AssignmentListQuerySerializer],
        responses={
            200: TrainerAssignmentListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = AssignmentListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_assignments(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            status=str(payload.get("status") or ""),
            trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
            member_id=payload.get("member_id"),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([assignment_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="trainer_assignments_create",
        request=AssignmentCreateSerializer,
        responses={
            201: TrainerAssignmentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = AssignmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        row = create_assignment(
            gym=request.gym,
            actor=request.gym_membership,
            trainer_staff_profile_id=payload["trainer_staff_profile_id"],
            member_id=payload["member_id"],
            effective_from=payload["effective_from"],
            effective_until=payload.get("effective_until"),
            reason=str(payload.get("reason") or ""),
            **_audit_context(request),
        )
        return success_response(
            assignment_payload(row),
            status=201,
            message="Trainer assignment created",
        )


class TrainerAssignmentDetailView(TrainerAPIView):
    @extend_schema(
        operation_id="trainer_assignments_retrieve",
        responses={
            200: TrainerAssignmentEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        row = get_visible_assignment(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            assignment_id=id,
        )
        return success_response(assignment_payload(row))


class TrainerAssignmentEndView(TrainerCommandView):
    @extend_schema(
        operation_id="trainer_assignments_end",
        request=AssignmentEndSerializer,
        responses={
            200: TrainerAssignmentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        serializer = AssignmentEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        row = end_assignment(
            gym=request.gym,
            actor=request.gym_membership,
            assignment_id=id,
            effective_until=payload["effective_until"],
            reason=str(payload["reason"]),
            **_audit_context(request),
        )
        return success_response(assignment_payload(row), message="Trainer assignment ended")


class TrainerAssignmentCancelView(TrainerCommandView):
    @extend_schema(
        operation_id="trainer_assignments_cancel",
        request=AssignmentCancelSerializer,
        responses={
            200: TrainerAssignmentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        serializer = AssignmentCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = cancel_assignment(
            gym=request.gym,
            actor=request.gym_membership,
            assignment_id=id,
            reason=str(serializer.validated_data["reason"]),
            **_audit_context(request),
        )
        return success_response(assignment_payload(row), message="Trainer assignment cancelled")


class ServicePackageListCreateView(TrainerAPIView):
    required_permission = "pt.schedule.view"

    def get_permissions(self):
        self.required_permission = (
            "pt.schedule.manage" if self.request.method == "POST" else "pt.schedule.view"
        )
        return super().get_permissions()

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [ServicePackageCommandThrottle()]
        return []

    @extend_schema(
        operation_id="service_packages_list",
        parameters=[ServicePackageListQuerySerializer],
        responses={
            200: ServicePackageListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ServicePackageListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        is_active = payload["is_active"] if "is_active" in payload else None
        rows, meta = page_service_packages(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            package_type=str(payload.get("type") or ""),
            is_active=is_active,
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response(
            [service_package_payload(row) for row in rows],
            meta=meta,
        )

    @extend_schema(
        operation_id="service_packages_create",
        request=ServicePackageWriteSerializer,
        responses={
            201: ServicePackageEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ServicePackageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        row = create_service_package(
            gym=request.gym,
            actor=request.gym_membership,
            package_type=str(payload["type"]),
            code=str(payload["code"]),
            name=str(payload["name"]),
            session_count=int(payload["session_count"]),
            validity_value=int(payload["validity_value"]),
            validity_unit=str(payload["validity_unit"]),
            price=payload["price"],
            class_type_id=payload.get("class_type_id"),
            **_audit_context(request),
        )
        return success_response(
            service_package_payload(row),
            status=201,
            message="Service package created",
        )

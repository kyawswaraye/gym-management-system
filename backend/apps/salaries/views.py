"""Published salary period routes, including one full void."""

from __future__ import annotations

import re
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

from apps.cashbook.serializers import transaction_payload
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, IdempotentResult, run_idempotent
from apps.expenses.serializers import expense_payload
from apps.files.throttling import MembershipWindowThrottle
from apps.salaries.selectors import get_period, page_periods
from apps.salaries.serializers import (
    ApproveSalarySerializer,
    CancelSalarySerializer,
    EmptyCommandSerializer,
    RejectSalarySerializer,
    SalaryApprovalEnvelopeSerializer,
    SalaryLinesReplaceSerializer,
    SalaryPeriodEnvelopeSerializer,
    SalaryPeriodListEnvelopeSerializer,
    SalaryPeriodListQuerySerializer,
    SalaryPeriodUpdateSerializer,
    SalaryPeriodWriteSerializer,
    SalaryVoidEnvelopeSerializer,
    VoidSalarySerializer,
    period_payload,
)
from apps.salaries.services import (
    approve_period,
    cancel_period,
    command_route,
    create_period,
    reject_period,
    replace_lines,
    submit_period,
    update_period,
    void_period,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission, TrainerFinanceClosed

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this salary command.",
)


class SalaryCommandThrottle(MembershipWindowThrottle):
    scope = "salaries_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


def require_idempotency_key(header: str | None) -> str:
    if header is None or _IDEMPOTENCY_KEY.fullmatch(header) is None:
        raise ValidationError({"Idempotency-Key": ["Enter a valid idempotency key."]})
    return header


def _stored_response(result: IdempotentResult) -> HttpResponse:
    return HttpResponse(result.body, status=result.status, content_type="application/json")


def _audit_context(request: Request) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", "")),
    }


class SalaryAPIView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_feature = "finance"
    required_permission = "salaries.view"


class SalaryCommandView(SalaryAPIView):
    throttle_classes = [SalaryCommandThrottle]

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


class SalaryPeriodListCreateView(SalaryCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "salaries.create" if self.request.method == "POST" else "salaries.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="salary_periods_list",
        parameters=[SalaryPeriodListQuerySerializer],
        responses={
            200: SalaryPeriodListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = SalaryPeriodListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_periods(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                status=str(payload.get("status", "")),
                business_date=payload.get("business_date"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([period_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="salary_periods_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=SalaryPeriodWriteSerializer,
        responses={
            201: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = SalaryPeriodWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            period = create_period(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": period_payload(period),
                "message": "Salary period created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="SalaryPeriod",
                resource_id=period.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(period_id=None, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class SalaryPeriodDetailView(SalaryCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "salaries.update" if self.request.method == "PATCH" else "salaries.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="salary_periods_retrieve",
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, period_id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            period = get_period(
                gym_id=request.gym.id,
                period_id=period_id,
                actor_membership_id=request.gym_membership.id,
            )
        return success_response(period_payload(period))

    @extend_schema(
        operation_id="salary_periods_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=SalaryPeriodUpdateSerializer,
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = SalaryPeriodUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = {"salary_period_id": str(period_id), **serializer.validated_data}

        def mutate() -> IdempotentResponse:
            period = update_period(
                gym=request.gym,
                actor=request.gym_membership,
                period_id=period_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": period_payload(period),
                "message": "Salary period updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="SalaryPeriod",
                resource_id=period.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=command_route(period_id=period_id, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class SalaryPeriodLinesView(SalaryCommandView):
    required_permission = "salaries.update"

    @extend_schema(
        operation_id="salary_periods_replace_lines",
        parameters=[IDEMPOTENCY_HEADER],
        request=SalaryLinesReplaceSerializer,
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def put(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = SalaryLinesReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        lines = list(serializer.validated_data["lines"])
        body = {"salary_period_id": str(period_id), "lines": lines}

        def mutate() -> IdempotentResponse:
            period = replace_lines(
                gym=request.gym,
                actor=request.gym_membership,
                period_id=period_id,
                lines=lines,
                **context,
            )
            rendered = {
                "success": True,
                "data": period_payload(period),
                "message": "Salary lines replaced",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="SalaryPeriod",
                resource_id=period.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PUT",
            route=command_route(period_id=period_id, action="lines"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class SalaryPeriodSubmitView(SalaryCommandView):
    required_permission = "salaries.submit"

    @extend_schema(
        operation_id="salary_periods_submit",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, period_id: UUID) -> HttpResponse:
        EmptyCommandSerializer(data=request.data).is_valid(raise_exception=True)
        return _period_command(
            request,
            period_id=period_id,
            action="submit",
            message="Salary period submitted",
            command=submit_period,
            fields={},
        )


class SalaryPeriodApproveView(SalaryCommandView):
    required_permission = "salaries.approve"

    @extend_schema(
        operation_id="salary_periods_approve",
        parameters=[IDEMPOTENCY_HEADER],
        request=ApproveSalarySerializer,
        responses={
            200: SalaryApprovalEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = ApproveSalarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        mappings = list(serializer.validated_data.get("cash_sessions", []))
        body = {"salary_period_id": str(period_id), "cash_sessions": mappings}

        def mutate() -> IdempotentResponse:
            period, expenses, movements = approve_period(
                gym=request.gym,
                actor=request.gym_membership,
                period_id=period_id,
                cash_sessions=mappings,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "salary_period": period_payload(period),
                    "expenses": [expense_payload(expense) for expense in expenses],
                    "cash_transactions": [transaction_payload(movement) for movement in movements],
                },
                "message": "Salary period approved",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="SalaryPeriod",
                resource_id=period.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(period_id=period_id, action="approve"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class SalaryPeriodVoidView(SalaryCommandView):
    required_permission = "salaries.void"

    @extend_schema(
        operation_id="salary_periods_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=VoidSalarySerializer,
        responses={
            200: SalaryVoidEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = VoidSalarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        mappings = list(serializer.validated_data.get("cash_sessions", []))
        fields = {
            "reason": serializer.validated_data["reason"],
            "business_date": serializer.validated_data.get("business_date"),
            "cash_sessions": mappings,
        }
        body = {"salary_period_id": str(period_id), **fields}

        def mutate() -> IdempotentResponse:
            period, reversals = void_period(
                gym=request.gym,
                actor=request.gym_membership,
                period_id=period_id,
                **fields,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "salary_period": period_payload(period),
                    "reversals": [
                        {
                            "line_id": line.id,
                            "reversal_expense_id": reversal.id,
                            "reversal_expense": expense_payload(reversal),
                            "cash_transaction": (
                                None if movement is None else transaction_payload(movement)
                            ),
                        }
                        for line, reversal, movement in reversals
                    ],
                },
                "message": "Salary period voided",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="SalaryPeriod",
                resource_id=period.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(period_id=period_id, action="void"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class SalaryPeriodRejectView(SalaryCommandView):
    required_permission = "salaries.approve"

    @extend_schema(
        operation_id="salary_periods_reject",
        parameters=[IDEMPOTENCY_HEADER],
        request=RejectSalarySerializer,
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = RejectSalarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _period_command(
            request,
            period_id=period_id,
            action="reject",
            message="Salary period rejected",
            command=reject_period,
            fields={"reason": serializer.validated_data["rejection_reason"]},
        )


class SalaryPeriodCancelView(SalaryCommandView):
    required_permission = "salaries.update"

    @extend_schema(
        operation_id="salary_periods_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=CancelSalarySerializer,
        responses={
            200: SalaryPeriodEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, period_id: UUID) -> HttpResponse:
        serializer = CancelSalarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _period_command(
            request,
            period_id=period_id,
            action="cancel",
            message="Salary period cancelled",
            command=cancel_period,
            fields={"reason": serializer.validated_data["reason"]},
        )


def _period_command(
    request: Request,
    *,
    period_id: UUID,
    action: str,
    message: str,
    command: Any,
    fields: dict[str, Any],
) -> HttpResponse:
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    context = _audit_context(request)
    body = {"salary_period_id": str(period_id), **fields}

    def mutate() -> IdempotentResponse:
        period = command(
            gym=request.gym,
            actor=request.gym_membership,
            period_id=period_id,
            **fields,
            **context,
        )
        rendered = {"success": True, "data": period_payload(period), "message": message}
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="SalaryPeriod",
            resource_id=period.id,
        )

    result = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=command_route(period_id=period_id, action=action),
        idempotency_key=key,
        body=body,
        mutate=mutate,
        request=request,
    )
    return _stored_response(result)

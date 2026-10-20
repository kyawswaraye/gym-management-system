"""Published expense-category and expense routes."""

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

from apps.cashbook.serializers import session_payload, transaction_payload
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, IdempotentResult, run_idempotent
from apps.expenses.selectors import get_expense, page_categories, page_expenses
from apps.expenses.serializers import (
    ApproveExpenseSerializer,
    CancelExpenseSerializer,
    EmptyCommandSerializer,
    ExpenseApprovalEnvelopeSerializer,
    ExpenseCategoryEnvelopeSerializer,
    ExpenseCategoryListEnvelopeSerializer,
    ExpenseCategoryListQuerySerializer,
    ExpenseCategoryUpdateSerializer,
    ExpenseCategoryWriteSerializer,
    ExpenseEnvelopeSerializer,
    ExpenseListEnvelopeSerializer,
    ExpenseListQuerySerializer,
    ExpenseUpdateSerializer,
    ExpenseVoidEnvelopeSerializer,
    ExpenseWriteSerializer,
    RejectExpenseSerializer,
    VoidExpenseSerializer,
    category_payload,
    expense_payload,
)
from apps.expenses.services import (
    approve_expense,
    cancel_expense,
    category_route,
    command_route,
    create_category,
    create_expense_draft,
    reject_expense,
    submit_expense,
    update_category,
    update_expense_draft,
    void_expense,
)
from apps.files.throttling import MembershipWindowThrottle
from apps.users.permissions import RequiresEntitlement, RequiresPermission, TrainerFinanceClosed

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this expense command.",
)


class ExpenseCommandThrottle(MembershipWindowThrottle):
    scope = "expenses_command"

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


class ExpenseAPIView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_feature = "finance"
    required_permission = "expenses.view"


class ExpenseCommandView(ExpenseAPIView):
    throttle_classes = [ExpenseCommandThrottle]

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


class ExpenseCategoryListCreateView(ExpenseCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "expense_categories.manage" if self.request.method == "POST" else "expenses.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="expense_categories_list",
        parameters=[ExpenseCategoryListQuerySerializer],
        responses={
            200: ExpenseCategoryListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ExpenseCategoryListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_categories(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                is_active=payload.get("is_active"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([category_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="expense_categories_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ExpenseCategoryWriteSerializer,
        responses={
            201: ExpenseCategoryEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ExpenseCategoryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            category = create_category(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": category_payload(category),
                "message": "Expense category created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="ExpenseCategory",
                resource_id=category.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=category_route(category_id=None),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ExpenseCategoryDetailView(ExpenseCommandView):
    required_permission = "expense_categories.manage"

    @extend_schema(
        operation_id="expense_categories_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=ExpenseCategoryUpdateSerializer,
        responses={
            200: ExpenseCategoryEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, category_id: UUID) -> HttpResponse:
        serializer = ExpenseCategoryUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = {"category_id": str(category_id), **serializer.validated_data}

        def mutate() -> IdempotentResponse:
            category = update_category(
                gym=request.gym,
                actor=request.gym_membership,
                category_id=category_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": category_payload(category),
                "message": "Expense category updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="ExpenseCategory",
                resource_id=category.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=category_route(category_id=category_id),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ExpenseListCreateView(ExpenseCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "expenses.create" if self.request.method == "POST" else "expenses.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="expenses_list",
        parameters=[ExpenseListQuerySerializer],
        responses={
            200: ExpenseListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ExpenseListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_expenses(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                status=str(payload.get("status", "")),
                category_id=payload.get("category"),
                payment_method=str(payload.get("payment_method", "")),
                business_date=payload.get("business_date"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([expense_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="expenses_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ExpenseWriteSerializer,
        responses={
            201: ExpenseEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ExpenseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            expense = create_expense_draft(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": expense_payload(expense),
                "message": "Expense draft created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="Expense",
                resource_id=expense.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(expense_id=None, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ExpenseDetailView(ExpenseCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "expenses.update" if self.request.method == "PATCH" else "expenses.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="expenses_retrieve",
        responses={
            200: ExpenseEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, expense_id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            expense = get_expense(
                gym_id=request.gym.id,
                expense_id=expense_id,
                actor_membership_id=request.gym_membership.id,
            )
        return success_response(expense_payload(expense))

    @extend_schema(
        operation_id="expenses_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=ExpenseUpdateSerializer,
        responses={
            200: ExpenseEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, expense_id: UUID) -> HttpResponse:
        serializer = ExpenseUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = {"expense_id": str(expense_id), **serializer.validated_data}

        def mutate() -> IdempotentResponse:
            expense = update_expense_draft(
                gym=request.gym,
                actor=request.gym_membership,
                expense_id=expense_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": expense_payload(expense),
                "message": "Expense draft updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Expense",
                resource_id=expense.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=command_route(expense_id=expense_id, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ExpenseSubmitView(ExpenseCommandView):
    required_permission = "expenses.submit"

    @extend_schema(
        operation_id="expenses_submit",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: ExpenseEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, expense_id: UUID) -> HttpResponse:
        EmptyCommandSerializer(data=request.data).is_valid(raise_exception=True)
        return _expense_command(
            request,
            expense_id=expense_id,
            action="submit",
            message="Expense submitted",
            command=submit_expense,
            fields={},
        )


class ExpenseApproveView(ExpenseCommandView):
    required_permission = "expenses.approve"

    @extend_schema(
        operation_id="expenses_approve",
        parameters=[IDEMPOTENCY_HEADER],
        request=ApproveExpenseSerializer,
        responses={
            200: ExpenseApprovalEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, expense_id: UUID) -> HttpResponse:
        serializer = ApproveExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        cash_session_id = serializer.validated_data.get("cash_session_id")
        body = {"expense_id": str(expense_id), "cash_session_id": cash_session_id}

        def mutate() -> IdempotentResponse:
            expense, session, movement = approve_expense(
                gym=request.gym,
                actor=request.gym_membership,
                expense_id=expense_id,
                cash_session_id=cash_session_id,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "expense": expense_payload(expense),
                    "cash_session": None if session is None else session_payload(session),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                },
                "message": "Expense approved",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Expense",
                resource_id=expense.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(expense_id=expense_id, action="approve"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ExpenseRejectView(ExpenseCommandView):
    required_permission = "expenses.approve"

    @extend_schema(
        operation_id="expenses_reject",
        parameters=[IDEMPOTENCY_HEADER],
        request=RejectExpenseSerializer,
        responses={
            200: ExpenseEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, expense_id: UUID) -> HttpResponse:
        serializer = RejectExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _expense_command(
            request,
            expense_id=expense_id,
            action="reject",
            message="Expense rejected",
            command=reject_expense,
            fields={"reason": serializer.validated_data["rejection_reason"]},
        )


class ExpenseCancelView(ExpenseCommandView):
    required_permission = "expenses.update"

    @extend_schema(
        operation_id="expenses_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=CancelExpenseSerializer,
        responses={
            200: ExpenseEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, expense_id: UUID) -> HttpResponse:
        serializer = CancelExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _expense_command(
            request,
            expense_id=expense_id,
            action="cancel",
            message="Expense cancelled",
            command=cancel_expense,
            fields={"reason": serializer.validated_data["reason"]},
        )


class ExpenseVoidView(ExpenseCommandView):
    required_permission = "expenses.void"

    @extend_schema(
        operation_id="expenses_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=VoidExpenseSerializer,
        responses={
            200: ExpenseVoidEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, expense_id: UUID) -> HttpResponse:
        serializer = VoidExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        fields = {
            "reason": payload["reason"],
            "business_date": payload.get("business_date"),
            "cash_session_id": payload.get("cash_session_id"),
        }
        body = {"expense_id": str(expense_id), **fields}

        def mutate() -> IdempotentResponse:
            original, reversal, movement = void_expense(
                gym=request.gym,
                actor=request.gym_membership,
                expense_id=expense_id,
                **fields,
                **context,
            )
            session = None if movement is None else movement.cash_session
            rendered = {
                "success": True,
                "data": {
                    "original_expense": expense_payload(original),
                    "reversal_expense": expense_payload(reversal),
                    "cash_session": None if session is None else session_payload(session),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                },
                "message": "Expense voided",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Expense",
                resource_id=reversal.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(expense_id=expense_id, action="void"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


def _expense_command(
    request: Request,
    *,
    expense_id: UUID,
    action: str,
    message: str,
    command: Any,
    fields: dict[str, Any],
) -> HttpResponse:
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    context = _audit_context(request)
    body = {"expense_id": str(expense_id), **fields}

    def mutate() -> IdempotentResponse:
        expense = command(
            gym=request.gym,
            actor=request.gym_membership,
            expense_id=expense_id,
            **fields,
            **context,
        )
        rendered = {"success": True, "data": expense_payload(expense), "message": message}
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="Expense",
            resource_id=expense.id,
        )

    result = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=command_route(expense_id=expense_id, action=action),
        idempotency_key=key,
        body=body,
        mutate=mutate,
        request=request,
    )
    return _stored_response(result)

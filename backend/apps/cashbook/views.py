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

from apps.cashbook.selectors import (
    get_visible_session,
    page_cash_sessions,
    page_cash_transactions,
)
from apps.cashbook.serializers import (
    AdjustCashSessionSerializer,
    CashCommandEnvelopeSerializer,
    CashSessionEnvelopeSerializer,
    CashSessionListEnvelopeSerializer,
    CashSessionListQuerySerializer,
    CashSessionSerializer,
    CashTransactionListEnvelopeSerializer,
    CashTransactionListQuerySerializer,
    CashTransactionSerializer,
    OpenCashSessionSerializer,
    ReconcileCashSessionSerializer,
    money_text,
    session_payload,
    transaction_payload,
)
from apps.cashbook.services import (
    assert_current_business_date,
    assert_owner_may_adjust,
    audit_context,
    command_route,
    open_cash_session,
    reconcile_cash_session,
    record_manual_adjustment,
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
    description="High-entropy key reused only for retries of this cash command.",
)


class CashbookCommandThrottle(MembershipWindowThrottle):
    scope = "cashbook_command"

    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None


def require_idempotency_key(header: str | None) -> str:
    if header is None or _IDEMPOTENCY_KEY.fullmatch(header) is None:
        raise ValidationError({"Idempotency-Key": ["Enter a valid idempotency key."]})
    return header


def _render_command(
    session: Any,
    transactions: list[Any],
    *,
    message: str,
    status: int,
) -> IdempotentResponse:
    payload = {
        "success": True,
        "data": {
            "session": session_payload(session),
            "transactions": [transaction_payload(item) for item in transactions],
        },
        "message": message,
    }
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(payload),
        resource_type="CashSession",
        resource_id=session.id,
    )


def _stored_response(result: IdempotentResult) -> HttpResponse:
    return HttpResponse(result.body, status=result.status, content_type="application/json")


class CashbookAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "finance"
    required_permission = "cashbook.view"


class CashCommandView(CashbookAPIView):
    """Authenticate in a short transaction, then let the idempotency runner commit."""

    throttle_classes = [CashbookCommandThrottle]

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


class CashSessionListView(CashbookAPIView):
    @extend_schema(
        operation_id="cash_sessions_list",
        parameters=[CashSessionListQuerySerializer],
        responses={
            200: CashSessionListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = CashSessionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_cash_sessions(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            status=str(payload.get("status", "")),
            register_code=str(payload.get("register_code", "")),
            business_date=payload.get("business_date"),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response(CashSessionSerializer(rows, many=True).data, meta=meta)


class OpenCashSessionView(CashCommandView):
    required_permission = "cashbook.open"

    @extend_schema(
        operation_id="cash_sessions_open",
        parameters=[IDEMPOTENCY_HEADER],
        request=OpenCashSessionSerializer,
        responses={
            201: CashCommandEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = OpenCashSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            assert_current_business_date(gym=request.gym, business_date=payload["business_date"])
        body = {
            "register_code": payload["register_code"],
            "business_date": payload["business_date"].isoformat(),
            "opening_cash": money_text(payload["opening_cash"]),
        }
        context = audit_context(request)

        def mutate() -> IdempotentResponse:
            session = open_cash_session(
                gym=request.gym,
                actor=request.gym_membership,
                register_code=str(payload["register_code"]),
                business_date=payload["business_date"],
                opening_cash=payload["opening_cash"],
                **context,
            )
            return _render_command(session, [], message="Cash session opened", status=201)

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route="/api/v1/cash-sessions/open/",
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class CashSessionDetailView(CashbookAPIView):
    @extend_schema(
        operation_id="cash_sessions_retrieve",
        responses={
            200: CashSessionEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        session = get_visible_session(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            session_id=id,
            permission_code="cashbook.view",
        )
        return success_response(CashSessionSerializer(session).data)


class AdjustCashSessionView(CashCommandView):
    required_permission = "cashbook.adjust"

    @extend_schema(
        operation_id="cash_sessions_adjust",
        parameters=[IDEMPOTENCY_HEADER],
        request=AdjustCashSessionSerializer,
        responses={
            201: CashCommandEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        serializer = AdjustCashSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            session = get_visible_session(
                gym_id=request.gym.id,
                actor=request.gym_membership,
                session_id=id,
                permission_code="cashbook.adjust",
            )
            currency_mismatch = payload["currency"] != session.currency
            date_mismatch = payload["business_date"] != session.business_date
            if currency_mismatch or date_mismatch:
                errors = {}
                if payload["currency"] != session.currency:
                    errors["currency"] = ["Use the cash session currency."]
                if payload["business_date"] != session.business_date:
                    errors["business_date"] = ["Use the cash session business date."]
                raise ValidationError(errors)
            assert_owner_may_adjust(request.gym_membership)
        body = {
            "cash_session_id": str(id),
            "direction": payload["direction"],
            "amount": money_text(payload["amount"]),
            "currency": payload["currency"],
            "business_date": payload["business_date"].isoformat(),
            "description": payload["description"],
            "reason": payload["reason"],
        }
        context = audit_context(request)

        def mutate() -> IdempotentResponse:
            session, movement = record_manual_adjustment(
                gym=request.gym,
                actor=request.gym_membership,
                session_id=id,
                direction=str(payload["direction"]),
                amount=payload["amount"],
                currency=str(payload["currency"]),
                business_date=payload["business_date"],
                description=str(payload["description"]),
                reason=str(payload["reason"]),
                **context,
            )
            return _render_command(
                session,
                [movement],
                message="Manual adjustment recorded",
                status=201,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(session_id=id, action="adjustments"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ReconcileCashSessionView(CashCommandView):
    required_permission = "cashbook.reconcile"

    @extend_schema(
        operation_id="cash_sessions_reconcile",
        parameters=[IDEMPOTENCY_HEADER],
        request=ReconcileCashSessionSerializer,
        responses={
            200: CashCommandEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        serializer = ReconcileCashSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            get_visible_session(
                gym_id=request.gym.id,
                actor=request.gym_membership,
                session_id=id,
                permission_code="cashbook.reconcile",
            )
        body = {
            "cash_session_id": str(id),
            "actual_closing": money_text(payload["actual_closing"]),
            "difference_reason": str(payload.get("difference_reason", "")),
        }
        context = audit_context(request)

        def mutate() -> IdempotentResponse:
            session = reconcile_cash_session(
                gym=request.gym,
                actor=request.gym_membership,
                session_id=id,
                actual_closing=payload["actual_closing"],
                difference_reason=str(payload.get("difference_reason", "")),
                **context,
            )
            return _render_command(session, [], message="Cash session reconciled", status=200)

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(session_id=id, action="reconcile"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class CashTransactionListView(CashbookAPIView):
    @extend_schema(
        operation_id="cash_transactions_list",
        parameters=[CashTransactionListQuerySerializer],
        responses={
            200: CashTransactionListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = CashTransactionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_cash_transactions(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            cash_session_id=payload.get("cash_session_id"),
            business_date=payload.get("business_date"),
            source_type=str(payload.get("source_type", "")),
            direction=str(payload.get("direction", "")),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response(CashTransactionSerializer(rows, many=True).data, meta=meta)

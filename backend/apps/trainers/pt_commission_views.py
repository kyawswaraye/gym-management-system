"""Published commission list, detail, and record-payment routes."""

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
from apps.expenses.serializers import expense_payload
from apps.files.throttling import MembershipWindowThrottle
from apps.trainers.pt_commission_services import command_route, record_pt_commission_payment
from apps.trainers.selectors import commission_payments, get_visible_commission, page_pt_commissions
from apps.trainers.serializers import (
    PTCommissionDetailEnvelopeSerializer,
    PTCommissionListEnvelopeSerializer,
    PTCommissionListQuerySerializer,
    PTCommissionPaymentEnvelopeSerializer,
    PTCommissionPaymentWriteSerializer,
    commission_payload,
    commission_payment_payload,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this commission payout.",
)


class PTCommissionCommandThrottle(MembershipWindowThrottle):
    scope = "pt_commission_command"

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


class PTCommissionAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "pt.commissions.view"


class PTCommissionCommandView(PTCommissionAPIView):
    throttle_classes = [PTCommissionCommandThrottle]

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


class PTCommissionListView(PTCommissionAPIView):
    @extend_schema(
        operation_id="pt_commissions_list",
        parameters=[PTCommissionListQuerySerializer],
        responses={
            200: PTCommissionListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = PTCommissionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_pt_commissions(
                gym=request.gym,
                actor=request.gym_membership,
                status=str(payload.get("status") or ""),
                trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
                service_date_from=payload.get("service_date_from"),
                service_date_to=payload.get("service_date_to"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([commission_payload(row) for row in rows], meta=meta)


class PTCommissionDetailView(PTCommissionAPIView):
    @extend_schema(
        operation_id="pt_commissions_retrieve",
        responses={
            200: PTCommissionDetailEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            row = get_visible_commission(
                gym=request.gym,
                actor=request.gym_membership,
                commission_id=id,
            )
            payments = commission_payments(row)
        payload = commission_payload(row)
        payload["payments"] = [commission_payment_payload(item) for item in payments]
        return success_response(payload)


class PTCommissionRecordPaymentView(PTCommissionCommandView):
    required_permission = "pt.commissions.pay"

    @extend_schema(
        operation_id="pt_commissions_record_payment",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTCommissionPaymentWriteSerializer,
        responses={
            200: PTCommissionPaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> HttpResponse:
        serializer = PTCommissionPaymentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        fields = serializer.validated_data
        body = {
            "amount": fields["amount"],
            "business_date": fields["business_date"],
            "payment_method": fields["payment_method"],
            "cash_session_id": fields.get("cash_session_id"),
            "reference_number": fields.get("reference_number", ""),
            "policy_note": fields.get("policy_note", ""),
        }

        def mutate() -> IdempotentResponse:
            before, after, payment, expense, movement = record_pt_commission_payment(
                gym=request.gym,
                actor=request.gym_membership,
                commission_id=id,
                amount=fields["amount"],
                business_date=fields["business_date"],
                payment_method=fields["payment_method"],
                cash_session_id=fields.get("cash_session_id"),
                reference_number=str(fields.get("reference_number", "")),
                policy_note=str(fields.get("policy_note", "")),
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "commission_before": commission_payload(before),
                    "commission_after": commission_payload(after),
                    "payment": commission_payment_payload(payment),
                    "expense": expense_payload(expense),
                    "cash_session": None
                    if movement is None
                    else session_payload(movement.cash_session),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                },
                "message": "Commission payout recorded",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTCommissionPayment",
                resource_id=payment.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(commission_id=id),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

"""Published payment, receipt, and daily-sales routes."""

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
from apps.files.throttling import MembershipWindowThrottle
from apps.members.payment_selectors import (
    daily_sales,
    get_payment,
    get_receipt,
    page_payments,
    page_receipts,
)
from apps.members.payment_serializers import (
    ApprovePaymentSerializer,
    CancelPaymentSerializer,
    DailySalesEnvelopeSerializer,
    DailySalesQuerySerializer,
    EmptyCommandSerializer,
    PaymentApprovalEnvelopeSerializer,
    PaymentCorrectionEnvelopeSerializer,
    PaymentEnvelopeSerializer,
    PaymentListEnvelopeSerializer,
    PaymentListQuerySerializer,
    PaymentUpdateSerializer,
    PaymentWriteSerializer,
    ReceiptEnvelopeSerializer,
    ReceiptListEnvelopeSerializer,
    ReceiptListQuerySerializer,
    RefundPaymentSerializer,
    RejectPaymentSerializer,
    VoidPaymentSerializer,
    VoidReceiptSerializer,
    money_text,
    payment_payload,
    receipt_payload,
    settlement_payload,
)
from apps.members.payment_services import (
    approve_payment,
    cancel_payment,
    command_route,
    create_payment_draft,
    refund_payment,
    reject_payment,
    submit_payment,
    update_payment_draft,
    void_payment,
    void_receipt,
)
from apps.members.receipt_rendering import receipt_download_response
from apps.users.permissions import RequiresEntitlement, RequiresEntitlements, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this payment command.",
)


class PaymentCommandThrottle(MembershipWindowThrottle):
    scope = "payments_command"

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


class PaymentAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "finance"
    required_permission = "payments.view"


class PaymentCommandView(PaymentAPIView):
    throttle_classes = [PaymentCommandThrottle]

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


class PaymentListCreateView(PaymentCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "payments.create" if self.request.method == "POST" else "payments.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="payments_list",
        parameters=[PaymentListQuerySerializer],
        responses={
            200: PaymentListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = PaymentListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_payments(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                status=str(payload.get("status", "")),
                member_id=payload.get("member"),
                membership_id=payload.get("membership"),
                purpose=str(payload.get("purpose", "")),
                method=str(payload.get("method", "")),
                business_date=payload.get("business_date"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([payment_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="payments_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=PaymentWriteSerializer,
        responses={
            201: PaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = PaymentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            payment = create_payment_draft(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": payment_payload(payment),
                "message": "Payment draft created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="Payment",
                resource_id=payment.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(payment_id=None, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PaymentDetailView(PaymentCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "payments.update" if self.request.method == "PATCH" else "payments.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="payments_retrieve",
        responses={
            200: PaymentEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, payment_id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            payment = get_payment(gym_id=request.gym.id, payment_id=payment_id)
        return success_response(payment_payload(payment))

    @extend_schema(
        operation_id="payments_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=PaymentUpdateSerializer,
        responses={
            200: PaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = PaymentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = {"payment_id": str(payment_id), **serializer.validated_data}

        def mutate() -> IdempotentResponse:
            payment = update_payment_draft(
                gym=request.gym,
                actor=request.gym_membership,
                payment_id=payment_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": payment_payload(payment),
                "message": "Payment draft updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Payment",
                resource_id=payment.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=command_route(payment_id=payment_id, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PaymentSubmitView(PaymentCommandView):
    required_permission = "payments.submit"

    @extend_schema(
        operation_id="payments_submit",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: PaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        EmptyCommandSerializer(data=request.data).is_valid(raise_exception=True)
        return _payment_command(
            request,
            payment_id=payment_id,
            action="submit",
            message="Payment submitted",
            command=submit_payment,
            fields={},
        )


class PaymentApproveView(PaymentCommandView):
    required_permission = "payments.approve"

    @extend_schema(
        operation_id="payments_approve",
        parameters=[IDEMPOTENCY_HEADER],
        request=ApprovePaymentSerializer,
        responses={
            200: PaymentApprovalEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = ApprovePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        cash_session_id = serializer.validated_data.get("cash_session_id")
        body = {"payment_id": str(payment_id), "cash_session_id": cash_session_id}

        def mutate() -> IdempotentResponse:
            payment, receipt, session, movement, settlement = approve_payment(
                gym=request.gym,
                actor=request.gym_membership,
                payment_id=payment_id,
                cash_session_id=cash_session_id,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "payment": payment_payload(payment),
                    "receipt": receipt_payload(receipt),
                    "cash_session": None if session is None else session_payload(session),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                    "membership_settlement": (
                        None
                        if settlement is None
                        else settlement_payload(
                            membership_id=settlement["membership_id"],
                            payment_status=str(settlement["payment_status"]),
                            paid_amount=settlement["paid_amount"],
                            balance_amount=settlement["balance_amount"],
                        )
                    ),
                },
                "message": "Payment approved",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Payment",
                resource_id=payment.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(payment_id=payment_id, action="approve"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PaymentRejectView(PaymentCommandView):
    required_permission = "payments.approve"

    @extend_schema(
        operation_id="payments_reject",
        parameters=[IDEMPOTENCY_HEADER],
        request=RejectPaymentSerializer,
        responses={
            200: PaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = RejectPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _payment_command(
            request,
            payment_id=payment_id,
            action="reject",
            message="Payment rejected",
            command=reject_payment,
            fields={"reason": serializer.validated_data["rejection_reason"]},
        )


class PaymentCancelView(PaymentCommandView):
    required_permission = "payments.update"

    @extend_schema(
        operation_id="payments_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=CancelPaymentSerializer,
        responses={
            200: PaymentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = CancelPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _payment_command(
            request,
            payment_id=payment_id,
            action="cancel",
            message="Payment cancelled",
            command=cancel_payment,
            fields={"reason": serializer.validated_data["reason"]},
        )


class PaymentVoidView(PaymentCommandView):
    required_permission = "payments.void"

    @extend_schema(
        operation_id="payments_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=VoidPaymentSerializer,
        responses={
            200: PaymentCorrectionEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = VoidPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        return _correction_command(
            request,
            payment_id=payment_id,
            action="void",
            message="Payment voided",
            command=void_payment,
            fields={
                "reason": payload["reason"],
                "business_date": payload.get("business_date"),
                "cash_session_id": payload.get("cash_session_id"),
            },
        )


class PaymentRefundView(PaymentCommandView):
    required_permission = "payments.refund"

    @extend_schema(
        operation_id="payments_refund",
        parameters=[IDEMPOTENCY_HEADER],
        request=RefundPaymentSerializer,
        responses={
            200: PaymentCorrectionEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, payment_id: UUID) -> HttpResponse:
        serializer = RefundPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        return _correction_command(
            request,
            payment_id=payment_id,
            action="refund",
            message="Payment refunded",
            command=refund_payment,
            fields={
                "amount": payload["amount"],
                "reason": payload["reason"],
                "method": payload["method"],
                "business_date": payload["business_date"],
                "reference_number": str(payload.get("reference_number") or ""),
                "cash_session_id": payload.get("cash_session_id"),
            },
        )


class ReceiptListView(PaymentAPIView):
    required_permission = "receipts.view"

    @extend_schema(
        operation_id="receipts_list",
        parameters=[ReceiptListQuerySerializer],
        responses={
            200: ReceiptListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReceiptListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        rows, meta = page_receipts(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            status=str(payload.get("status", "")),
            payment_id=payload.get("payment"),
            member_id=payload.get("member"),
            business_date=payload.get("business_date"),
            limit=int(payload["limit"]),
            after=str(payload.get("after", "")),
        )
        return success_response([receipt_payload(row) for row in rows], meta=meta)


class ReceiptDetailView(PaymentAPIView):
    required_permission = "receipts.view"

    @extend_schema(
        operation_id="receipts_retrieve",
        responses={
            200: ReceiptEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, receipt_id: UUID) -> Response:
        receipt = get_receipt(gym_id=request.gym.id, receipt_id=receipt_id)
        return success_response(receipt_payload(receipt))


class ReceiptVoidView(PaymentCommandView):
    required_permission = "receipts.void"

    @extend_schema(
        operation_id="receipts_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=VoidReceiptSerializer,
        responses={
            200: ReceiptEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, receipt_id: UUID) -> HttpResponse:
        serializer = VoidReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        reason = serializer.validated_data["reason"]
        body = {"receipt_id": str(receipt_id), "reason": reason}

        def mutate() -> IdempotentResponse:
            receipt = void_receipt(
                gym=request.gym,
                actor=request.gym_membership,
                receipt_id=receipt_id,
                reason=reason,
                **context,
            )
            rendered = {
                "success": True,
                "data": receipt_payload(receipt),
                "message": "Receipt voided",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Receipt",
                resource_id=receipt.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=f"/api/v1/receipts/{receipt_id}/void/",
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ReceiptDownloadView(PaymentAPIView):
    required_permission = "receipts.view"

    @extend_schema(
        operation_id="receipts_download",
        responses={
            (200, "text/html"): OpenApiTypes.BINARY,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, receipt_id: UUID) -> HttpResponse:
        receipt = get_receipt(gym_id=request.gym.id, receipt_id=receipt_id)
        return receipt_download_response(receipt)


class DailySalesView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlements]
    required_permission = "reports.financial"
    required_features = ("reports", "finance")

    @extend_schema(
        operation_id="sales_daily",
        parameters=[DailySalesQuerySerializer],
        responses={
            200: DailySalesEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = DailySalesQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = daily_sales(
            gym_id=request.gym.id,
            business_date=query.validated_data["date"],
            currency=request.gym.currency,
        )
        return success_response(payload)


def _correction_command(
    request: Request,
    *,
    payment_id: UUID,
    action: str,
    message: str,
    command: Any,
    fields: dict[str, Any],
) -> HttpResponse:
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    context = _audit_context(request)
    body = {"payment_id": str(payment_id), **fields}

    def mutate() -> IdempotentResponse:
        result = command(
            gym=request.gym,
            actor=request.gym_membership,
            payment_id=payment_id,
            **fields,
            **context,
        )
        settlement = result["settlement"]
        rendered = {
            "success": True,
            "data": {
                "original_payment": payment_payload(result["original"]),
                "correction_payment": payment_payload(result["correction"]),
                "original_receipt": (
                    None
                    if result["original_receipt"] is None
                    else receipt_payload(result["original_receipt"])
                ),
                "correction_receipt": receipt_payload(result["correction_receipt"]),
                "cash_transactions": [
                    transaction_payload(movement) for movement in result["cash_transactions"]
                ],
                "refundable_amount": money_text(result["refundable_amount"]),
                "membership_settlement": (
                    None
                    if settlement is None
                    else settlement_payload(
                        membership_id=settlement["membership_id"],
                        payment_status=str(settlement["payment_status"]),
                        paid_amount=settlement["paid_amount"],
                        balance_amount=settlement["balance_amount"],
                    )
                ),
            },
            "message": message,
        }
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="Payment",
            resource_id=result["correction"].id,
        )

    stored = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=command_route(payment_id=payment_id, action=action),
        idempotency_key=key,
        body=body,
        mutate=mutate,
        request=request,
    )
    return _stored_response(stored)


def _payment_command(
    request: Request,
    *,
    payment_id: UUID,
    action: str,
    message: str,
    command: Any,
    fields: dict[str, Any],
) -> HttpResponse:
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    context = _audit_context(request)
    body = {"payment_id": str(payment_id), **fields}

    def mutate() -> IdempotentResponse:
        payment = command(
            gym=request.gym,
            actor=request.gym_membership,
            payment_id=payment_id,
            **fields,
            **context,
        )
        rendered = {"success": True, "data": payment_payload(payment), "message": message}
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="Payment",
            resource_id=payment.id,
        )

    result = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=command_route(payment_id=payment_id, action=action),
        idempotency_key=key,
        body=body,
        mutate=mutate,
        request=request,
    )
    return _stored_response(result)

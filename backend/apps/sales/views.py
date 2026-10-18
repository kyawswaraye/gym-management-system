"""Published retail sale routes, including one full void."""

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
from apps.files.throttling import MembershipWindowThrottle
from apps.inventory.quantities import format_quantity
from apps.members.payment_serializers import money_text, payment_payload, receipt_payload
from apps.sales.selectors import get_sale, page_sales
from apps.sales.serializers import (
    ApproveRetailSaleSerializer,
    CancelRetailSaleSerializer,
    EmptyCommandSerializer,
    RejectRetailSaleSerializer,
    RetailSaleApprovalEnvelopeSerializer,
    RetailSaleEnvelopeSerializer,
    RetailSaleListEnvelopeSerializer,
    RetailSaleListQuerySerializer,
    RetailSaleUpdateSerializer,
    RetailSaleVoidEnvelopeSerializer,
    RetailSaleWriteSerializer,
    VoidRetailSaleSerializer,
    sale_payload,
)
from apps.sales.services import (
    approve_sale,
    cancel_sale,
    command_route,
    create_sale,
    reject_sale,
    submit_sale,
    update_sale,
    void_sale,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this retail command.",
)


class RetailCommandThrottle(MembershipWindowThrottle):
    scope = "retail_command"

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


class RetailAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "finance"
    required_permission = "retail.view"


class RetailCommandView(RetailAPIView):
    throttle_classes = [RetailCommandThrottle]

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


class RetailSaleListCreateView(RetailCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "retail.create" if self.request.method == "POST" else "retail.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="retail_sales_list",
        parameters=[RetailSaleListQuerySerializer],
        responses={
            200: RetailSaleListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = RetailSaleListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_sales(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                status=str(payload.get("status", "")),
                business_date=payload.get("business_date"),
                member_id=payload.get("member"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([sale_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="retail_sales_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=RetailSaleWriteSerializer,
        responses={
            201: RetailSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = RetailSaleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            sale = create_sale(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": sale_payload(sale),
                "message": "Retail sale created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="RetailSale",
                resource_id=sale.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=None, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class RetailSaleDetailView(RetailCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "retail.update" if self.request.method == "PATCH" else "retail.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="retail_sales_retrieve",
        responses={
            200: RetailSaleEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, sale_id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            sale = get_sale(
                gym_id=request.gym.id,
                sale_id=sale_id,
                actor_membership_id=request.gym_membership.id,
            )
        return success_response(sale_payload(sale))

    @extend_schema(
        operation_id="retail_sales_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=RetailSaleUpdateSerializer,
        responses={
            200: RetailSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, sale_id: UUID) -> HttpResponse:
        serializer = RetailSaleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = {"retail_sale_id": str(sale_id), **serializer.validated_data}

        def mutate() -> IdempotentResponse:
            sale = update_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=sale_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": sale_payload(sale),
                "message": "Retail sale updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="RetailSale",
                resource_id=sale.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=command_route(sale_id=sale_id, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class RetailSaleSubmitView(RetailCommandView):
    required_permission = "retail.submit"

    @extend_schema(
        operation_id="retail_sales_submit",
        parameters=[IDEMPOTENCY_HEADER],
        request=EmptyCommandSerializer,
        responses={
            200: RetailSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, sale_id: UUID) -> HttpResponse:
        EmptyCommandSerializer(data=request.data).is_valid(raise_exception=True)
        return _sale_command(
            request,
            sale_id=sale_id,
            action="submit",
            message="Retail sale submitted",
            command=submit_sale,
            fields={},
        )


class RetailSaleApproveView(RetailCommandView):
    required_permission = "retail.approve"

    @extend_schema(
        operation_id="retail_sales_approve",
        parameters=[IDEMPOTENCY_HEADER],
        request=ApproveRetailSaleSerializer,
        responses={
            200: RetailSaleApprovalEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, sale_id: UUID) -> HttpResponse:
        serializer = ApproveRetailSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        cash_session_id = serializer.validated_data.get("cash_session_id")
        body = {"retail_sale_id": str(sale_id), "cash_session_id": cash_session_id}

        def mutate() -> IdempotentResponse:
            sale, payment, receipt, movement = approve_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=sale_id,
                cash_session_id=cash_session_id,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "retail_sale": sale_payload(sale),
                    "payment": payment_payload(payment),
                    "receipt": receipt_payload(receipt),
                    "cash_transaction": (
                        None if movement is None else transaction_payload(movement)
                    ),
                },
                "message": "Retail sale approved",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="RetailSale",
                resource_id=sale.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=sale_id, action="approve"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class RetailSaleVoidView(RetailCommandView):
    required_permission = "retail.void"

    @extend_schema(
        operation_id="retail_sales_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=VoidRetailSaleSerializer,
        responses={
            200: RetailSaleVoidEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, sale_id: UUID) -> HttpResponse:
        serializer = VoidRetailSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        fields = {
            "reason": serializer.validated_data["reason"],
            "business_date": serializer.validated_data.get("business_date"),
            "cash_session_id": serializer.validated_data.get("cash_session_id"),
        }
        body = {"retail_sale_id": str(sale_id), **fields}

        def mutate() -> IdempotentResponse:
            result = void_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=sale_id,
                **fields,
                **context,
            )
            movement = result["cash_transaction"]
            rendered = {
                "success": True,
                "data": {
                    "retail_sale": sale_payload(result["sale"]),
                    "original_payment": payment_payload(result["original"]),
                    "reversal_payment": payment_payload(result["correction"]),
                    "original_receipt": (
                        None
                        if result["original_receipt"] is None
                        else receipt_payload(result["original_receipt"])
                    ),
                    "reversal_receipt": receipt_payload(result["correction_receipt"]),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                    "stock_movements": [
                        {
                            "id": row.id,
                            "retail_sale_line_id": row.retail_sale_line_id,
                            "product_id": row.product_id,
                            "quantity": format_quantity(row.quantity),
                            "movement_type": row.movement_type,
                            "unit_cost_snapshot": money_text(row.unit_cost_snapshot),
                            "business_date": row.business_date,
                            "reverses_movement_id": row.reverses_movement_id,
                        }
                        for row in result["stock_movements"]
                    ],
                },
                "message": "Retail sale voided",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="RetailSale",
                resource_id=result["sale"].id,
            )

        stored = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=sale_id, action="void"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(stored)


class RetailSaleRejectView(RetailCommandView):
    required_permission = "retail.approve"

    @extend_schema(
        operation_id="retail_sales_reject",
        parameters=[IDEMPOTENCY_HEADER],
        request=RejectRetailSaleSerializer,
        responses={
            200: RetailSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, sale_id: UUID) -> HttpResponse:
        serializer = RejectRetailSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _sale_command(
            request,
            sale_id=sale_id,
            action="reject",
            message="Retail sale rejected",
            command=reject_sale,
            fields={"reason": serializer.validated_data["rejection_reason"]},
        )


class RetailSaleCancelView(RetailCommandView):
    required_permission = "retail.update"

    @extend_schema(
        operation_id="retail_sales_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=CancelRetailSaleSerializer,
        responses={
            200: RetailSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, sale_id: UUID) -> HttpResponse:
        serializer = CancelRetailSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _sale_command(
            request,
            sale_id=sale_id,
            action="cancel",
            message="Retail sale cancelled",
            command=cancel_sale,
            fields={"reason": serializer.validated_data["reason"]},
        )


def _sale_command(
    request: Request,
    *,
    sale_id: UUID,
    action: str,
    message: str,
    command: Any,
    fields: dict[str, Any],
) -> HttpResponse:
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    context = _audit_context(request)
    body = {"retail_sale_id": str(sale_id), **fields}

    def mutate() -> IdempotentResponse:
        sale = command(
            gym=request.gym,
            actor=request.gym_membership,
            sale_id=sale_id,
            **fields,
            **context,
        )
        rendered = {"success": True, "data": sale_payload(sale), "message": message}
        return IdempotentResponse(
            status=200,
            body=JSONRenderer().render(rendered),
            resource_type="RetailSale",
            resource_id=sale.id,
        )

    result = run_idempotent(
        gym_id=request.gym.id,
        actor_membership_id=request.gym_membership.id,
        http_method="POST",
        route=command_route(sale_id=sale_id, action=action),
        idempotency_key=key,
        body=body,
        mutate=mutate,
        request=request,
    )
    return _stored_response(result)

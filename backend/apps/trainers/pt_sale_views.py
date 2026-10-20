"""Published draft-only personal-training sale routes."""

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
from apps.members.payment_serializers import payment_payload, receipt_payload
from apps.trainers.pt_sale_services import (
    approve_pt_sale,
    cancel_pt_sale,
    command_route,
    create_pt_sale,
    reject_pt_sale,
    submit_pt_sale,
    update_pt_sale,
)
from apps.trainers.pt_sale_void_services import void_pt_sale
from apps.trainers.selectors import get_visible_pt_sale, page_pt_sales
from apps.trainers.serializers import (
    PTSaleApprovalEnvelopeSerializer,
    PTSaleApproveSerializer,
    PTSaleCancelSerializer,
    PTSaleEnvelopeSerializer,
    PTSaleListEnvelopeSerializer,
    PTSaleListQuerySerializer,
    PTSaleRejectSerializer,
    PTSaleUpdateSerializer,
    PTSaleVoidEnvelopeSerializer,
    PTSaleVoidSerializer,
    PTSaleWriteSerializer,
    commission_payload,
    commission_payment_payload,
    ledger_entry_payload,
    member_package_payload,
    pt_sale_payload,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this PT sale command.",
)


class PTSaleCommandThrottle(MembershipWindowThrottle):
    scope = "pt_sale_command"

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


class PTSaleAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "pt.sales.view"


class PTSaleCommandView(PTSaleAPIView):
    throttle_classes = [PTSaleCommandThrottle]

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


class PTSaleListCreateView(PTSaleCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "pt.sales.create" if self.request.method == "POST" else "pt.sales.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="pt_sales_list",
        parameters=[PTSaleListQuerySerializer],
        responses={
            200: PTSaleListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = PTSaleListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_pt_sales(
                gym=request.gym,
                actor=request.gym_membership,
                status=str(payload.get("status") or ""),
                member_id=payload.get("member_id"),
                trainer_staff_profile_id=payload.get("trainer_staff_profile_id"),
                service_date_from=payload.get("service_date_from"),
                service_date_to=payload.get("service_date_to"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([pt_sale_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="pt_sales_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleWriteSerializer,
        responses={
            201: PTSaleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = PTSaleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            row = create_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                member_id=body["member_id"],
                trainer_staff_profile_id=body["trainer_staff_profile_id"],
                service_date=body["service_date"],
                description=str(body["description"]),
                gross_fee=body["gross_fee"],
                currency=str(body["currency"]),
                payment=body.get("payment"),
                service_package_id=body.get("service_package_id"),
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_sale_payload(row),
                "message": "PT sale draft created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
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


class PTSaleDetailView(PTSaleCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "pt.sales.update" if self.request.method == "PATCH" else "pt.sales.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="pt_sales_retrieve",
        responses={
            200: PTSaleEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            row = get_visible_pt_sale(gym=request.gym, actor=request.gym_membership, sale_id=id)
        return success_response(pt_sale_payload(row))

    @extend_schema(
        operation_id="pt_sales_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleUpdateSerializer,
        responses={
            200: PTSaleEnvelopeSerializer,
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
        serializer = PTSaleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            row = update_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                fields=body,
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_sale_payload(row),
                "message": "PT sale draft updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=command_route(sale_id=id, action=""),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTSaleCancelView(PTSaleCommandView):
    required_permission = "pt.sales.update"

    @extend_schema(
        operation_id="pt_sales_cancel",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleCancelSerializer,
        responses={
            200: PTSaleEnvelopeSerializer,
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
        serializer = PTSaleCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            row = cancel_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                reason=str(body["reason"]),
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_sale_payload(row),
                "message": "PT sale cancelled",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=id, action="cancel"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTSaleSubmitView(PTSaleCommandView):
    required_permission = "pt.sales.submit"

    @extend_schema(
        operation_id="pt_sales_submit",
        parameters=[IDEMPOTENCY_HEADER],
        request=None,
        responses={
            200: PTSaleEnvelopeSerializer,
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
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)

        def mutate() -> IdempotentResponse:
            row = submit_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_sale_payload(row),
                "message": "PT sale submitted",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=id, action="submit"),
            idempotency_key=key,
            body={},
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTSaleApproveView(PTSaleCommandView):
    required_permission = "pt.sales.approve"

    @extend_schema(
        operation_id="pt_sales_approve",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleApproveSerializer,
        responses={
            200: PTSaleApprovalEnvelopeSerializer,
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
        serializer = PTSaleApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        cash_session_id = serializer.validated_data.get("cash_session_id")
        body = {"cash_session_id": cash_session_id}

        def mutate() -> IdempotentResponse:
            row, payment, receipt, movement, commission, granted = approve_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                cash_session_id=cash_session_id,
                **context,
            )
            if granted is not None:
                row.granted_package_id = granted.id
            rendered = {
                "success": True,
                "data": {
                    "sale": pt_sale_payload(row),
                    "payment": None if payment is None else payment_payload(payment),
                    "receipt": None if receipt is None else receipt_payload(receipt),
                    "cash_session": (
                        None if movement is None else session_payload(movement.cash_session)
                    ),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                    "commission": commission_payload(commission),
                    "member_service_package": (
                        None if granted is None else member_package_payload(granted)
                    ),
                },
                "message": "PT sale approved",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=id, action="approve"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTSaleRejectView(PTSaleCommandView):
    required_permission = "pt.sales.approve"

    @extend_schema(
        operation_id="pt_sales_reject",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleRejectSerializer,
        responses={
            200: PTSaleEnvelopeSerializer,
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
        serializer = PTSaleRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            row = reject_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                rejection_reason=str(body["rejection_reason"]),
                **context,
            )
            rendered = {
                "success": True,
                "data": pt_sale_payload(row),
                "message": "PT sale rejected",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=row.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=id, action="reject"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class PTSaleVoidView(PTSaleCommandView):
    required_permission = "pt.sales.void"

    @extend_schema(
        operation_id="pt_sales_void",
        parameters=[IDEMPOTENCY_HEADER],
        request=PTSaleVoidSerializer,
        responses={
            200: PTSaleVoidEnvelopeSerializer,
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
        serializer = PTSaleVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        payload = serializer.validated_data
        reversals = sorted(
            payload["payout_reversals"],
            key=lambda row: str(row["commission_payment_id"]),
        )
        cash_session_id = payload.get("cash_session_id")
        body = {
            "reason": payload["reason"],
            "business_date": payload["business_date"].isoformat(),
            "cash_session_id": None if cash_session_id is None else str(cash_session_id),
            "payout_reversals": [
                {
                    "commission_payment_id": str(row["commission_payment_id"]),
                    "cash_session_id": (
                        None if row["cash_session_id"] is None else str(row["cash_session_id"])
                    ),
                }
                for row in reversals
            ],
        }

        def mutate() -> IdempotentResponse:
            result = void_pt_sale(
                gym=request.gym,
                actor=request.gym_membership,
                sale_id=id,
                reason=str(payload["reason"]),
                business_date=payload["business_date"],
                cash_session_id=cash_session_id,
                payout_reversals=reversals,
                **context,
            )
            result["sale"].granted_package_id = (
                None if result["package"] is None else result["package"].id
            )
            payment = result["payment"]
            rendered = {
                "success": True,
                "data": {
                    "sale": pt_sale_payload(result["sale"]),
                    "commission": commission_payload(result["commission"]),
                    "payouts": [commission_payment_payload(row) for row in result["payouts"]],
                    "payment": None
                    if payment is None
                    else {
                        "original": payment_payload(payment["original"]),
                        "correction": payment_payload(payment["correction"]),
                        "original_receipt": (
                            None
                            if payment["original_receipt"] is None
                            else receipt_payload(payment["original_receipt"])
                        ),
                        "correction_receipt": receipt_payload(payment["correction_receipt"]),
                        "cash_transaction": (
                            None
                            if payment["cash_transaction"] is None
                            else transaction_payload(payment["cash_transaction"])
                        ),
                    },
                    "member_service_package": (
                        None
                        if result["package"] is None
                        else member_package_payload(result["package"])
                    ),
                    "ledger_entry": (
                        None if result["refund"] is None else ledger_entry_payload(result["refund"])
                    ),
                },
                "message": "PT sale voided",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="PTSale",
                resource_id=result["sale"].id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=command_route(sale_id=id, action="void"),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

"""Published catalog and manual stock routes."""

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

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import IdempotentResponse, IdempotentResult, run_idempotent
from apps.files.throttling import MembershipWindowThrottle
from apps.inventory.selectors import (
    get_product,
    page_categories,
    page_low_stock,
    page_movements,
    page_products,
)
from apps.inventory.serializers import (
    LowStockQuerySerializer,
    ProductCategoryEnvelopeSerializer,
    ProductCategoryListEnvelopeSerializer,
    ProductCategoryListQuerySerializer,
    ProductCategoryWriteSerializer,
    ProductEnvelopeSerializer,
    ProductListEnvelopeSerializer,
    ProductListQuerySerializer,
    ProductUpdateSerializer,
    ProductWriteSerializer,
    StockAdjustmentEnvelopeSerializer,
    StockAdjustSerializer,
    StockMovementListEnvelopeSerializer,
    StockMovementListQuerySerializer,
    category_payload,
    movement_payload,
    product_payload,
)
from apps.inventory.services import (
    adjust_route,
    adjust_stock,
    category_route,
    create_category,
    create_product,
    product_route,
    update_product,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission, TrainerFinanceClosed

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this inventory command.",
)


class InventoryCommandThrottle(MembershipWindowThrottle):
    scope = "inventory_command"

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


class InventoryAPIView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_feature = "finance"
    required_permission = "inventory.view"


class InventoryCommandView(InventoryAPIView):
    throttle_classes = [InventoryCommandThrottle]

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


class ProductCategoryListCreateView(InventoryCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "inventory.manage" if self.request.method == "POST" else "inventory.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="product_categories_list",
        parameters=[ProductCategoryListQuerySerializer],
        responses={
            200: ProductCategoryListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ProductCategoryListQuerySerializer(data=request.query_params)
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
        operation_id="product_categories_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ProductCategoryWriteSerializer,
        responses={
            201: ProductCategoryEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ProductCategoryWriteSerializer(data=request.data)
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
                "message": "Product category created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="ProductCategory",
                resource_id=category.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=category_route(),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ProductListCreateView(InventoryCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "inventory.manage" if self.request.method == "POST" else "inventory.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="products_list",
        parameters=[ProductListQuerySerializer],
        responses={
            200: ProductListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ProductListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_products(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                category_id=payload.get("category_id"),
                is_active=payload.get("is_active"),
                sku=str(payload.get("sku", "")),
                barcode=str(payload.get("barcode", "")),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([product_payload(row) for row in rows], meta=meta)

    @extend_schema(
        operation_id="products_create",
        parameters=[IDEMPOTENCY_HEADER],
        request=ProductWriteSerializer,
        responses={
            201: ProductEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            product = create_product(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": product_payload(product),
                "message": "Product created",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="Product",
                resource_id=product.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=product_route(product_id=None),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class ProductLowStockView(InventoryAPIView):
    @extend_schema(
        operation_id="products_low_stock",
        parameters=[LowStockQuerySerializer],
        responses={
            200: ProductListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = LowStockQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_low_stock(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                category_id=payload.get("category_id"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([product_payload(row) for row in rows], meta=meta)


class ProductDetailView(InventoryCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "inventory.manage" if self.request.method == "PATCH" else "inventory.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="products_retrieve",
        responses={
            200: ProductEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, product_id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            product = get_product(gym_id=request.gym.id, product_id=product_id)
        return success_response(product_payload(product))

    @extend_schema(
        operation_id="products_update",
        parameters=[IDEMPOTENCY_HEADER],
        request=ProductUpdateSerializer,
        responses={
            200: ProductEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, product_id: UUID) -> HttpResponse:
        serializer = ProductUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            product = update_product(
                gym=request.gym,
                actor=request.gym_membership,
                product_id=product_id,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": product_payload(product),
                "message": "Product updated",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="Product",
                resource_id=product.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="PATCH",
            route=product_route(product_id=product_id),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class StockMovementListView(InventoryAPIView):
    @extend_schema(
        operation_id="stock_movements_list",
        parameters=[StockMovementListQuerySerializer],
        responses={
            200: StockMovementListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = StockMovementListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_movements(
                gym_id=request.gym.id,
                actor_membership_id=request.gym_membership.id,
                product_id=payload.get("product_id"),
                movement_type=str(payload.get("movement_type", "")),
                business_date=payload.get("business_date"),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([movement_payload(row) for row in rows], meta=meta)


class StockAdjustView(InventoryCommandView):
    required_permission = "inventory.adjust"

    @extend_schema(
        operation_id="stock_movements_adjust",
        parameters=[IDEMPOTENCY_HEADER],
        request=StockAdjustSerializer,
        responses={
            201: StockAdjustmentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> HttpResponse:
        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            movement, product = adjust_stock(
                gym=request.gym,
                actor=request.gym_membership,
                fields=serializer.validated_data,
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "movement": movement_payload(movement),
                    "product": product_payload(product),
                },
                "message": "Stock adjusted",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="StockMovement",
                resource_id=movement.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=adjust_route(),
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

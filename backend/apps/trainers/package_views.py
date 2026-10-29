"""Published member-package grant and ledger routes."""

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
from apps.trainers.package_services import adjust_ledger, grant_package_admin
from apps.trainers.selectors import (
    get_visible_member_package,
    page_member_packages,
    page_package_ledger,
)
from apps.trainers.serializers import (
    LedgerAdjustSerializer,
    LedgerListEnvelopeSerializer,
    MemberPackageEnvelopeSerializer,
    MemberPackageGrantEnvelopeSerializer,
    MemberPackageGrantSerializer,
    MemberPackageListEnvelopeSerializer,
    MemberPackageListQuerySerializer,
    PageQuerySerializer,
    ledger_entry_payload,
    member_package_payload,
    member_package_payloads,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="High-entropy key reused only for retries of this package command.",
)


class MemberPackageCommandThrottle(MembershipWindowThrottle):
    scope = "member_package_command"

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


class MemberPackageAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]
    required_feature = "pt"
    required_permission = "pt.schedule.view"


class MemberPackageCommandView(MemberPackageAPIView):
    throttle_classes = [MemberPackageCommandThrottle]

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


class MemberPackageListCreateView(MemberPackageCommandView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get_permissions(self):
        self.required_permission = (
            "pt.sessions.adjust" if self.request.method == "POST" else "pt.schedule.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="member_service_packages_list",
        parameters=[MemberPackageListQuerySerializer],
        responses={
            200: MemberPackageListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberPackageListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = query.validated_data
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            rows, meta = page_member_packages(
                gym=request.gym,
                actor=request.gym_membership,
                status=str(payload.get("status") or ""),
                member_id=payload.get("member_id"),
                package_type=str(payload.get("package_type") or ""),
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
            data = member_package_payloads(rows)
        return success_response(data, meta=meta)

    @extend_schema(
        operation_id="member_service_packages_grant",
        parameters=[IDEMPOTENCY_HEADER],
        request=MemberPackageGrantSerializer,
        responses={
            201: MemberPackageGrantEnvelopeSerializer,
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
        serializer = MemberPackageGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            grant, entry = grant_package_admin(
                gym=request.gym,
                actor=request.gym_membership,
                member_id=body["member_id"],
                service_package_id=body["service_package_id"],
                business_date=body["business_date"],
                complimentary=bool(body["complimentary"]),
                reason=str(body["reason"]),
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "member_service_package": member_package_payload(grant),
                    "ledger_entry": ledger_entry_payload(entry),
                },
                "message": "Member service package granted",
            }
            return IdempotentResponse(
                status=201,
                body=JSONRenderer().render(rendered),
                resource_type="MemberServicePackage",
                resource_id=grant.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route="/api/v1/member-service-packages/",
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)


class MemberPackageDetailView(MemberPackageAPIView):
    @extend_schema(
        operation_id="member_service_packages_retrieve",
        responses={
            200: MemberPackageEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            row = get_visible_member_package(
                gym=request.gym,
                actor=request.gym_membership,
                package_id=id,
            )
            data = member_package_payload(row)
        return success_response(data)


class MemberPackageLedgerView(MemberPackageAPIView):
    @extend_schema(
        operation_id="member_service_packages_ledger_list",
        parameters=[PageQuerySerializer],
        responses={
            200: LedgerListEnvelopeSerializer,
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
        with transaction.atomic():
            set_gym_context(gym_id=request.gym.id)
            _package, rows, meta = page_package_ledger(
                gym=request.gym,
                actor=request.gym_membership,
                package_id=id,
                limit=int(payload["limit"]),
                after=str(payload.get("after", "")),
            )
        return success_response([ledger_entry_payload(row) for row in rows], meta=meta)


class MemberPackageLedgerAdjustView(MemberPackageCommandView):
    required_permission = "pt.sessions.adjust"

    @extend_schema(
        operation_id="member_service_packages_ledger_adjust",
        parameters=[IDEMPOTENCY_HEADER],
        request=LedgerAdjustSerializer,
        responses={
            200: MemberPackageGrantEnvelopeSerializer,
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
        serializer = LedgerAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = require_idempotency_key(request.headers.get("Idempotency-Key"))
        context = _audit_context(request)
        body = dict(serializer.validated_data)

        def mutate() -> IdempotentResponse:
            grant, entry = adjust_ledger(
                gym=request.gym,
                actor=request.gym_membership,
                package_id=id,
                reverses_entry_id=body["reverses_entry_id"],
                reason=str(body["reason"]),
                **context,
            )
            rendered = {
                "success": True,
                "data": {
                    "member_service_package": member_package_payload(grant),
                    "ledger_entry": ledger_entry_payload(entry),
                },
                "message": "Session ledger corrected",
            }
            return IdempotentResponse(
                status=200,
                body=JSONRenderer().render(rendered),
                resource_type="ServiceSessionLedger",
                resource_id=entry.id,
            )

        result = run_idempotent(
            gym_id=request.gym.id,
            actor_membership_id=request.gym_membership.id,
            http_method="POST",
            route=f"/api/v1/member-service-packages/{id}/ledger/adjust/",
            idempotency_key=key,
            body=body,
            mutate=mutate,
            request=request,
        )
        return _stored_response(result)

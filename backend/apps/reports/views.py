"""Tenant dashboard and period report reads."""

from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.members.services import gym_today
from apps.reports.inventory_reports import (
    inventory_report,
    product_sales_report,
    stock_movement_report,
)
from apps.reports.ranges import month_bounds, resolve_preset
from apps.reports.selectors import (
    cash_book_report,
    check_in_report,
    class_report,
    membership_expiry_report,
    period_report,
    profit_loss_report,
    pt_session_report,
    tenant_dashboard,
    trainer_dashboard,
    trainer_session_report,
)
from apps.reports.serializers import (
    CashBookReportEnvelopeSerializer,
    CashBookReportSerializer,
    CheckInReportEnvelopeSerializer,
    CheckInReportSerializer,
    ClassReportEnvelopeSerializer,
    ClassReportSerializer,
    DailyReportQuerySerializer,
    InventoryReportEnvelopeSerializer,
    InventoryReportQuerySerializer,
    InventoryReportSerializer,
    MembershipExpiryReportEnvelopeSerializer,
    MembershipExpiryReportSerializer,
    MonthlyReportQuerySerializer,
    PeriodReportEnvelopeSerializer,
    PeriodReportSerializer,
    ProductSalesReportEnvelopeSerializer,
    ProductSalesReportQuerySerializer,
    ProductSalesReportSerializer,
    ProfitLossReportEnvelopeSerializer,
    ProfitLossReportSerializer,
    PTSessionReportEnvelopeSerializer,
    PTSessionReportSerializer,
    ReportRangeQuerySerializer,
    StockMovementReportEnvelopeSerializer,
    StockMovementReportSerializer,
    TenantDashboardEnvelopeSerializer,
    TenantDashboardQuerySerializer,
    TenantDashboardSerializer,
    TrainerDashboardEnvelopeSerializer,
    TrainerDashboardSerializer,
    TrainerSessionReportEnvelopeSerializer,
    TrainerSessionReportSerializer,
)
from apps.tenants.models import GymMembership, GymSettings
from apps.trainers.selectors import require_own_active_trainer_profile_id
from apps.users.permissions import (
    RequiresEntitlement,
    RequiresEntitlements,
    RequiresPermission,
    TrainerFinanceClosed,
)


class DashboardView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_permission = "reports.view"
    required_feature = "reports"

    @extend_schema(
        operation_id="tenant_dashboard_retrieve",
        parameters=[TenantDashboardQuerySerializer],
        responses={
            200: TenantDashboardEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = TenantDashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        today = gym_today(request.gym)
        settings = GymSettings.objects.filter(gym_id=request.gym.id).only("week_starts_on").first()
        week_starts_on = 1 if settings is None else int(settings.week_starts_on)
        if "range" in filters:
            date_from, date_to = resolve_preset(
                str(filters["range"]),
                today=today,
                week_starts_on=week_starts_on,
            )
        else:
            date_from = filters["date_from"]
            date_to = filters["date_to"]
        payload = tenant_dashboard(
            gym=request.gym,
            date_from=date_from,
            date_to=date_to,
            today=today,
            include_financial="reports.financial" in request.permission_codes,
        )
        return success_response(TenantDashboardSerializer(payload).data)


class _FinancialReportView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_permission = "reports.financial"
    required_feature = "reports"


class DailyReportView(_FinancialReportView):
    @extend_schema(
        operation_id="tenant_daily_report_retrieve",
        parameters=[DailyReportQuerySerializer],
        responses={
            200: PeriodReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = DailyReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        day = query.validated_data["date"]
        payload = period_report(
            gym=request.gym,
            date_from=day,
            date_to=day,
            generated_at=timezone.now(),
        )
        return success_response(PeriodReportSerializer(payload).data)


class MonthlyReportView(_FinancialReportView):
    @extend_schema(
        operation_id="tenant_monthly_report_retrieve",
        parameters=[MonthlyReportQuerySerializer],
        responses={
            200: PeriodReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MonthlyReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        date_from, date_to = month_bounds(
            int(query.validated_data["year"]),
            int(query.validated_data["month"]),
        )
        payload = period_report(
            gym=request.gym,
            date_from=date_from,
            date_to=date_to,
            generated_at=timezone.now(),
        )
        return success_response(PeriodReportSerializer(payload).data)


class CashBookReportView(_FinancialReportView):
    @extend_schema(
        operation_id="tenant_cash_book_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: CashBookReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = cash_book_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(CashBookReportSerializer(payload).data)


class ProfitLossReportView(_FinancialReportView):
    @extend_schema(
        operation_id="tenant_profit_loss_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: ProfitLossReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = profit_loss_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(ProfitLossReportSerializer(payload).data)


class _OperationalReportView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlement,
        TrainerFinanceClosed,
    ]
    required_permission = "reports.view"
    required_feature = "reports"


class MembershipExpiryReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_membership_expiry_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: MembershipExpiryReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = membership_expiry_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(MembershipExpiryReportSerializer(payload).data)


class CheckInReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_check_in_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: CheckInReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = check_in_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(CheckInReportSerializer(payload).data)


class PTSessionReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_pt_session_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: PTSessionReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = pt_session_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(PTSessionReportSerializer(payload).data)


class ClassReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_class_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: ClassReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = class_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(ClassReportSerializer(payload).data)


class ProductSalesReportView(_FinancialReportView):
    @extend_schema(
        operation_id="tenant_product_sales_report_retrieve",
        parameters=[ProductSalesReportQuerySerializer],
        responses={
            200: ProductSalesReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ProductSalesReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = product_sales_report(
            gym=request.gym,
            actor_membership_id=request.gym_membership.id,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            limit=int(query.validated_data["limit"]),
            after=str(query.validated_data.get("after", "")),
            generated_at=timezone.now(),
        )
        return success_response(ProductSalesReportSerializer(payload).data)


class StockMovementReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_stock_movement_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: StockMovementReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = stock_movement_report(
            gym=request.gym,
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(StockMovementReportSerializer(payload).data)


class InventoryReportView(_OperationalReportView):
    @extend_schema(
        operation_id="tenant_inventory_report_retrieve",
        parameters=[InventoryReportQuerySerializer],
        responses={
            200: InventoryReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = InventoryReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = inventory_report(
            gym=request.gym,
            actor_membership_id=request.gym_membership.id,
            limit=int(query.validated_data["limit"]),
            after=str(query.validated_data.get("after", "")),
            generated_at=timezone.now(),
        )
        return success_response(InventoryReportSerializer(payload).data)


class RequiresOwnActiveTrainerProfile(BasePermission):
    """404 unless the caller holds protected TRAINER and an active own profile.

    Place this after permission and entitlement checks. A missing grant or
    feature stays 403. Protected OWNER or MANAGER on the same membership does
    not replace TRAINER and does not widen the profile.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        actor = getattr(request, "gym_membership", None)
        gym = getattr(request, "gym", None)
        if not isinstance(actor, GymMembership) or gym is None:
            raise Http404
        request.trainer_staff_profile_id = require_own_active_trainer_profile_id(
            gym_id=gym.id,
            actor=actor,
        )
        return True


class _TrainerOwnReportView(APIView):
    permission_classes = [
        IsAuthenticated,
        RequiresPermission,
        RequiresEntitlements,
        RequiresOwnActiveTrainerProfile,
    ]
    required_permission = "reports.view"
    required_features = ("reports", "pt")


def _trainer_profile_id(request: Request) -> UUID:
    profile_id = getattr(request, "trainer_staff_profile_id", None)
    if not isinstance(profile_id, UUID):
        raise Http404
    return profile_id


def _dashboard_period(
    request: Request,
    filters: dict[str, object],
    *,
    generated_at: datetime,
) -> tuple[date, date, date]:
    """Resolve a preset or custom range the same way as the tenant dashboard.

    ``generated_at`` is the one instant captured by the caller. Gym settings are
    read once for every preset and custom range, so the handler stays at four
    queries after authorization. The selector then performs its own three.
    """

    today = timezone.localdate(generated_at, timezone=ZoneInfo(request.gym.timezone))
    settings = GymSettings.objects.filter(gym_id=request.gym.id).only("week_starts_on").first()
    week_starts_on = 1 if settings is None else int(settings.week_starts_on)
    if "range" in filters:
        date_from, date_to = resolve_preset(
            str(filters["range"]),
            today=today,
            week_starts_on=week_starts_on,
        )
    else:
        date_from = cast(date, filters["date_from"])
        date_to = cast(date, filters["date_to"])
    return date_from, date_to, today


class TrainerDashboardView(_TrainerOwnReportView):
    @extend_schema(
        operation_id="trainer_dashboard_retrieve",
        parameters=[TenantDashboardQuerySerializer],
        responses={
            200: TrainerDashboardEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = TenantDashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        generated_at = timezone.now()
        date_from, date_to, today = _dashboard_period(
            request,
            dict(query.validated_data),
            generated_at=generated_at,
        )
        payload = trainer_dashboard(
            gym=request.gym,
            trainer_staff_profile_id=_trainer_profile_id(request),
            date_from=date_from,
            date_to=date_to,
            today=today,
            generated_at=generated_at,
        )
        return success_response(TrainerDashboardSerializer(payload).data)


class TrainerSessionReportView(_TrainerOwnReportView):
    @extend_schema(
        operation_id="trainer_session_report_retrieve",
        parameters=[ReportRangeQuerySerializer],
        responses={
            200: TrainerSessionReportEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = ReportRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = trainer_session_report(
            gym=request.gym,
            trainer_staff_profile_id=_trainer_profile_id(request),
            date_from=query.validated_data["date_from"],
            date_to=query.validated_data["date_to"],
            generated_at=timezone.now(),
        )
        return success_response(TrainerSessionReportSerializer(payload).data)

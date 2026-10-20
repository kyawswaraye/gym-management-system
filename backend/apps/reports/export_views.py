"""Immediate CSV downloads for the published gym reports.

These routes do not create export jobs, rows, or files. Trainer dashboard and
session reports are not exported here.
"""

from __future__ import annotations

from typing import Any, cast

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.reports.csv_export import export_filename, range_dates, render_report_csv
from apps.reports.inventory_reports import (
    inventory_export,
    product_sales_export,
    stock_movement_report,
)
from apps.reports.ranges import month_bounds
from apps.reports.selectors import (
    cash_book_report,
    check_in_report,
    class_report,
    membership_expiry_report,
    period_report,
    profit_loss_report,
    pt_session_report,
)
from apps.reports.serializers import (
    DailyReportQuerySerializer,
    InventoryExportQuerySerializer,
    MonthlyReportQuerySerializer,
    ProductSalesExportQuerySerializer,
    ReportRangeQuerySerializer,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermissions, TrainerFinanceClosed

_CSV_RESPONSES = {
    (200, "text/csv"): OpenApiTypes.BINARY,
    400: ErrorEnvelopeSerializer,
    401: ErrorEnvelopeSerializer,
    403: ErrorEnvelopeSerializer,
    404: ErrorEnvelopeSerializer,
}


class _ReportExportView(APIView):
    permission_classes = (
        IsAuthenticated,
        RequiresPermissions,
        RequiresEntitlement,
        TrainerFinanceClosed,
    )
    required_feature = "reports"
    required_permissions: tuple[str, ...] = ()


class _FinancialReportExportView(_ReportExportView):
    required_permissions = ("reports.financial", "reports.export")


class _OperationalReportExportView(_ReportExportView):
    required_permissions = ("reports.view", "reports.export")


def _filters(request: Request, serializer_cls: type[Serializer[Any]]) -> dict[str, Any]:
    query = serializer_cls(data=request.query_params)
    query.is_valid(raise_exception=True)
    return cast(dict[str, Any], query.validated_data)


def _download(request: Request, kind: str, payload: dict[str, object]) -> HttpResponse:
    date_from, date_to = range_dates(payload)
    filename = export_filename(
        kind=kind,
        gym_code=str(request.gym.code),
        date_from=date_from,
        date_to=date_to,
    )
    response = HttpResponse(
        render_report_csv(kind, payload),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store, private"
    return response


class DailyReportExportView(_FinancialReportExportView):
    @extend_schema(
        operation_id="tenant_daily_report_export",
        parameters=[DailyReportQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        day = _filters(request, DailyReportQuerySerializer)["date"]
        payload = period_report(
            gym=request.gym,
            date_from=day,
            date_to=day,
            generated_at=timezone.now(),
        )
        return _download(request, "daily", payload)


class MonthlyReportExportView(_FinancialReportExportView):
    @extend_schema(
        operation_id="tenant_monthly_report_export",
        parameters=[MonthlyReportQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, MonthlyReportQuerySerializer)
        date_from, date_to = month_bounds(int(filters["year"]), int(filters["month"]))
        payload = period_report(
            gym=request.gym,
            date_from=date_from,
            date_to=date_to,
            generated_at=timezone.now(),
        )
        return _download(request, "monthly", payload)


class CashBookReportExportView(_FinancialReportExportView):
    @extend_schema(
        operation_id="tenant_cash_book_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = cash_book_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "cash-book", payload)


class ProfitLossReportExportView(_FinancialReportExportView):
    @extend_schema(
        operation_id="tenant_profit_loss_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = profit_loss_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "profit-loss", payload)


class ProductSalesReportExportView(_FinancialReportExportView):
    @extend_schema(
        operation_id="tenant_product_sales_report_export",
        parameters=[ProductSalesExportQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ProductSalesExportQuerySerializer)
        payload = product_sales_export(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "product-sales", payload)


class MembershipExpiryReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_membership_expiry_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = membership_expiry_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "membership-expiry", payload)


class CheckInReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_check_in_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = check_in_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "check-ins", payload)


class PTSessionReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_pt_session_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = pt_session_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "pt-sessions", payload)


class ClassReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_class_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = class_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "classes", payload)


class StockMovementReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_stock_movement_report_export",
        parameters=[ReportRangeQuerySerializer],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        filters = _filters(request, ReportRangeQuerySerializer)
        payload = stock_movement_report(
            gym=request.gym,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            generated_at=timezone.now(),
        )
        return _download(request, "stock-movements", payload)


class InventoryReportExportView(_OperationalReportExportView):
    @extend_schema(
        operation_id="tenant_inventory_report_export",
        parameters=[],
        responses=_CSV_RESPONSES,
    )
    def get(self, request: Request) -> HttpResponse:
        _filters(request, InventoryExportQuerySerializer)
        payload = inventory_export(gym=request.gym, generated_at=timezone.now())
        return _download(request, "inventory", payload)

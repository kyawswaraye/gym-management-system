"""Tenant dashboard query and response contracts.

The example in the API omits timezone and currency. Phase 6 requires both
explicitly, so this response adds them beside ``range`` and does not remove
any required operational or financial key. Money stays server-formatted.
``quantity_sold`` uses three decimal places because sale-line quantity is
``NUMERIC(18, 3)``; the two-decimal example is illustrative. Product rows are
the ranked top 10 from the selector, not every product in the range.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as calendar_date
from typing import Any, cast

from rest_framework import serializers

from apps.reports.ranges import (
    MAX_INCLUSIVE_DAYS,
    MAX_REPORT_ROWS,
    PRESETS,
    REPORT_MAX_DATE,
    REPORT_MIN_DATE,
    date_in_report_window,
    inclusive_days,
    month_bounds,
)
from apps.users.serializers import StrictSerializer


class TenantDashboardQuerySerializer(StrictSerializer):
    range = serializers.ChoiceField(choices=[(value, value) for value in PRESETS], required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        has_range = "range" in attrs
        has_from = "date_from" in attrs
        has_to = "date_to" in attrs
        if has_range and (has_from or has_to):
            raise serializers.ValidationError(
                {"range": ["Send a preset range or a custom date range, not both."]}
            )
        if has_range:
            return attrs
        if has_from != has_to:
            raise serializers.ValidationError({"date_from": ["Send both date_from and date_to."]})
        if not has_from or not has_to:
            raise serializers.ValidationError(
                {"range": ["Send a preset range or both date_from and date_to."]}
            )
        start = attrs["date_from"]
        end = attrs["date_to"]
        if not isinstance(start, calendar_date) or not isinstance(end, calendar_date):
            raise serializers.ValidationError({"date_from": ["Enter a valid date."]})
        if end < start:
            raise serializers.ValidationError(
                {"date_to": ["The end date cannot be before the start date."]}
            )
        if inclusive_days(start, end) > MAX_INCLUSIVE_DAYS:
            raise serializers.ValidationError(
                {"date_to": ["A custom range cannot exceed 366 days."]}
            )
        return attrs


class TenantDashboardRangeSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class TenantDashboardOperationalSerializer(serializers.Serializer):
    active_members = serializers.IntegerField()
    new_members = serializers.IntegerField()
    expired_memberships = serializers.IntegerField()
    check_ins = serializers.IntegerField()
    unique_attendees = serializers.IntegerField()
    currently_checked_in = serializers.IntegerField()
    expiring_within_3_days = serializers.IntegerField()
    expiring_within_7_days = serializers.IntegerField()
    expiring_within_30_days = serializers.IntegerField()
    pending_approvals = serializers.IntegerField()


class TenantDashboardRevenueSourceSerializer(serializers.Serializer):
    source = serializers.CharField()
    amount = serializers.CharField()


class TenantDashboardProductSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    name = serializers.CharField()
    quantity_sold = serializers.CharField()
    revenue = serializers.CharField()
    gross_profit = serializers.CharField()


class TenantDashboardExpenseCategorySerializer(serializers.Serializer):
    system_key = serializers.CharField()
    amount = serializers.CharField()


class TenantDashboardFinancialSerializer(serializers.Serializer):
    total_revenue = serializers.CharField()
    new_member_revenue = serializers.CharField()
    renewal_revenue = serializers.CharField()
    pt_revenue = serializers.CharField()
    class_revenue = serializers.CharField()
    product_revenue = serializers.CharField()
    other_revenue = serializers.CharField()
    pending_payment_member_count = serializers.IntegerField()
    pending_payment_amount = serializers.CharField()
    revenue_sources_ranked = TenantDashboardRevenueSourceSerializer(many=True)
    top_product_items = TenantDashboardProductSerializer(many=True)
    total_expenses = serializers.CharField()
    expenses_by_category = TenantDashboardExpenseCategorySerializer(many=True)
    staff_salary_expense = serializers.CharField()
    pt_commission_expense = serializers.CharField()
    net_profit = serializers.CharField()


class TenantDashboardSerializer(serializers.Serializer):
    range = TenantDashboardRangeSerializer()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    operational = TenantDashboardOperationalSerializer()
    financial = TenantDashboardFinancialSerializer(allow_null=True)


class TenantDashboardEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TenantDashboardSerializer()
    message = serializers.CharField()


class DailyReportQuerySerializer(StrictSerializer):
    date = serializers.DateField()

    def validate_date(self, value: calendar_date) -> calendar_date:
        if not date_in_report_window(value):
            raise serializers.ValidationError("The date is outside the supported reporting window.")
        return value


class MonthlyReportQuerySerializer(StrictSerializer):
    year = serializers.IntegerField(min_value=REPORT_MIN_DATE.year, max_value=REPORT_MAX_DATE.year)
    month = serializers.IntegerField(min_value=1, max_value=12)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start, end = month_bounds(int(attrs["year"]), int(attrs["month"]))
        if not date_in_report_window(start) or not date_in_report_window(end):
            raise serializers.ValidationError(
                {"year": ["The month is outside the supported reporting window."]}
            )
        if inclusive_days(start, end) > MAX_REPORT_ROWS:
            raise serializers.ValidationError({"month": ["A month cannot exceed 31 days."]})
        return attrs


class ReportGymSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()


class ReportRangeSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class ReportSourceStatusPolicySerializer(serializers.Serializer):
    revenue = serializers.CharField()
    expenses = serializers.CharField()
    operations = serializers.CharField()


class ReportDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    new_members = serializers.IntegerField()
    expired_memberships = serializers.IntegerField()
    check_ins = serializers.IntegerField()
    unique_attendees = serializers.IntegerField()
    new_member_revenue = serializers.CharField()
    renewal_revenue = serializers.CharField()
    pt_revenue = serializers.CharField()
    class_revenue = serializers.CharField()
    product_revenue = serializers.CharField()
    other_revenue = serializers.CharField()
    total_revenue = serializers.CharField()
    product_quantity = serializers.CharField()
    product_cogs = serializers.CharField()
    product_gross_profit = serializers.CharField()
    total_expenses = serializers.CharField()
    net_profit = serializers.CharField()


class ReportTotalsSerializer(serializers.Serializer):
    new_members = serializers.IntegerField()
    expired_memberships = serializers.IntegerField()
    check_ins = serializers.IntegerField()
    unique_attendees = serializers.IntegerField()
    new_member_revenue = serializers.CharField()
    renewal_revenue = serializers.CharField()
    pt_revenue = serializers.CharField()
    class_revenue = serializers.CharField()
    product_revenue = serializers.CharField()
    other_revenue = serializers.CharField()
    total_revenue = serializers.CharField()
    product_quantity = serializers.CharField()
    product_cogs = serializers.CharField()
    product_gross_profit = serializers.CharField()
    total_expenses = serializers.CharField()
    net_profit = serializers.CharField()
    staff_salary_expense = serializers.CharField()
    pt_commission_expense = serializers.CharField()
    expenses_by_category = TenantDashboardExpenseCategorySerializer(many=True)


class PeriodReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = ReportSourceStatusPolicySerializer()
    rows = ReportDaySerializer(many=True)
    totals = ReportTotalsSerializer()


class PeriodReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PeriodReportSerializer()
    message = serializers.CharField()


class _StrictQuerySerializer(StrictSerializer):
    def to_internal_value(self, data: Any) -> dict[str, Any]:
        errors: dict[str, list[str]] = {}
        if isinstance(data, Mapping):
            for field in sorted(set(data) - set(self.fields)):
                errors[str(field)] = ["This field is not allowed."]
            getlist = getattr(data, "getlist", None)
            if callable(getlist):
                for field in sorted(set(data) & set(self.fields)):
                    values = getlist(field)
                    if not isinstance(values, list):
                        continue
                    if len(values) > 1:
                        errors.setdefault(str(field), []).append("Duplicate value.")
                    elif any(value == "" for value in values):
                        errors.setdefault(str(field), []).append("This field may not be blank.")
        if errors:
            raise serializers.ValidationError(errors)
        return cast(dict[str, Any], super().to_internal_value(data))


class ReportRangeQuerySerializer(_StrictQuerySerializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs["date_from"]
        end = attrs["date_to"]
        if not isinstance(start, calendar_date) or not isinstance(end, calendar_date):
            raise serializers.ValidationError({"date_from": ["Enter a valid date."]})
        if not date_in_report_window(start) or not date_in_report_window(end):
            raise serializers.ValidationError(
                {"date_from": ["The date is outside the supported reporting window."]}
            )
        if end < start:
            raise serializers.ValidationError(
                {"date_to": ["The end date cannot be before the start date."]}
            )
        if inclusive_days(start, end) > MAX_INCLUSIVE_DAYS:
            raise serializers.ValidationError(
                {"date_to": ["A report range cannot exceed 366 days."]}
            )
        return attrs


class ProductSalesReportQuerySerializer(ReportRangeQuerySerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class ProductSalesExportQuerySerializer(ReportRangeQuerySerializer):
    """Product-sales CSV export accepts the report range and no page cursor."""


class InventoryReportQuerySerializer(_StrictQuerySerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class InventoryExportQuerySerializer(_StrictQuerySerializer):
    """Inventory CSV export accepts no date, limit, or page cursor."""


class CashBookSourceStatusPolicySerializer(serializers.Serializer):
    sessions = serializers.CharField()
    transactions = serializers.CharField()


class CashBookDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    session_count = serializers.IntegerField()
    open_session_count = serializers.IntegerField()
    closed_session_count = serializers.IntegerField()
    void_session_count = serializers.IntegerField()
    opening_cash = serializers.CharField()
    cash_in = serializers.CharField()
    cash_out = serializers.CharField()
    net_cash_flow = serializers.CharField()
    payment_in = serializers.CharField()
    payment_out = serializers.CharField()
    expense_in = serializers.CharField()
    expense_out = serializers.CharField()
    commission_in = serializers.CharField()
    commission_out = serializers.CharField()
    manual_in = serializers.CharField()
    manual_out = serializers.CharField()
    reversal_in = serializers.CharField()
    reversal_out = serializers.CharField()
    expected_closing = serializers.CharField()
    actual_closing = serializers.CharField()
    difference = serializers.CharField()


class CashBookTotalsSerializer(serializers.Serializer):
    session_count = serializers.IntegerField()
    open_session_count = serializers.IntegerField()
    closed_session_count = serializers.IntegerField()
    void_session_count = serializers.IntegerField()
    opening_cash = serializers.CharField()
    cash_in = serializers.CharField()
    cash_out = serializers.CharField()
    net_cash_flow = serializers.CharField()
    payment_in = serializers.CharField()
    payment_out = serializers.CharField()
    expense_in = serializers.CharField()
    expense_out = serializers.CharField()
    commission_in = serializers.CharField()
    commission_out = serializers.CharField()
    manual_in = serializers.CharField()
    manual_out = serializers.CharField()
    reversal_in = serializers.CharField()
    reversal_out = serializers.CharField()
    expected_closing = serializers.CharField()
    actual_closing = serializers.CharField()
    difference = serializers.CharField()


class CashBookReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = CashBookSourceStatusPolicySerializer()
    rows = CashBookDaySerializer(many=True)
    totals = CashBookTotalsSerializer()


class CashBookReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CashBookReportSerializer()
    message = serializers.CharField()


class ProfitLossDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    new_member_revenue = serializers.CharField()
    renewal_revenue = serializers.CharField()
    pt_revenue = serializers.CharField()
    class_revenue = serializers.CharField()
    product_revenue = serializers.CharField()
    other_revenue = serializers.CharField()
    total_revenue = serializers.CharField()
    product_cogs = serializers.CharField()
    product_gross_profit = serializers.CharField()
    total_expenses = serializers.CharField()
    net_profit = serializers.CharField()


class ProfitLossTotalsSerializer(serializers.Serializer):
    new_member_revenue = serializers.CharField()
    renewal_revenue = serializers.CharField()
    pt_revenue = serializers.CharField()
    class_revenue = serializers.CharField()
    product_revenue = serializers.CharField()
    other_revenue = serializers.CharField()
    total_revenue = serializers.CharField()
    product_cogs = serializers.CharField()
    product_gross_profit = serializers.CharField()
    total_expenses = serializers.CharField()
    net_profit = serializers.CharField()
    staff_salary_expense = serializers.CharField()
    pt_commission_expense = serializers.CharField()
    expenses_by_category = TenantDashboardExpenseCategorySerializer(many=True)


class ProfitLossReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = ReportSourceStatusPolicySerializer()
    rows = ProfitLossDaySerializer(many=True)
    totals = ProfitLossTotalsSerializer()


class ProfitLossReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProfitLossReportSerializer()
    message = serializers.CharField()


class MembershipExpiryDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    expiring_memberships = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    active_memberships = serializers.IntegerField()
    suspended_memberships = serializers.IntegerField()
    expired_memberships = serializers.IntegerField()


class MembershipExpiryTotalsSerializer(serializers.Serializer):
    expiring_memberships = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    active_memberships = serializers.IntegerField()
    suspended_memberships = serializers.IntegerField()
    expired_memberships = serializers.IntegerField()


class MembershipExpiryReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = MembershipExpiryDaySerializer(many=True)
    totals = MembershipExpiryTotalsSerializer()


class MembershipExpiryReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MembershipExpiryReportSerializer()
    message = serializers.CharField()


class CheckInReportDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    check_ins = serializers.IntegerField()
    unique_attendees = serializers.IntegerField()
    manual_check_ins = serializers.IntegerField()
    qr_check_ins = serializers.IntegerField()
    nfc_check_ins = serializers.IntegerField()
    biometric_check_ins = serializers.IntegerField()
    check_outs = serializers.IntegerField()
    open_visits_from_day = serializers.IntegerField()


class CheckInReportTotalsSerializer(serializers.Serializer):
    check_ins = serializers.IntegerField()
    unique_attendees = serializers.IntegerField()
    manual_check_ins = serializers.IntegerField()
    qr_check_ins = serializers.IntegerField()
    nfc_check_ins = serializers.IntegerField()
    biometric_check_ins = serializers.IntegerField()
    check_outs = serializers.IntegerField()
    open_visits_from_day = serializers.IntegerField()
    currently_checked_in = serializers.IntegerField()


class CheckInReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = CheckInReportDaySerializer(many=True)
    totals = CheckInReportTotalsSerializer()


class CheckInReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CheckInReportSerializer()
    message = serializers.CharField()


class PTSessionReportDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    unique_trainers = serializers.IntegerField()
    booked_sessions = serializers.IntegerField()
    attended_sessions = serializers.IntegerField()
    no_show_sessions = serializers.IntegerField()
    cancelled_sessions = serializers.IntegerField()
    metered_sessions = serializers.IntegerField()
    unmetered_sessions = serializers.IntegerField()


class PTSessionReportTotalsSerializer(serializers.Serializer):
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    unique_trainers = serializers.IntegerField()
    booked_sessions = serializers.IntegerField()
    attended_sessions = serializers.IntegerField()
    no_show_sessions = serializers.IntegerField()
    cancelled_sessions = serializers.IntegerField()
    metered_sessions = serializers.IntegerField()
    unmetered_sessions = serializers.IntegerField()


class PTSessionReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = PTSessionReportDaySerializer(many=True)
    totals = PTSessionReportTotalsSerializer()


class PTSessionReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTSessionReportSerializer()
    message = serializers.CharField()


class ClassReportDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    occurrences = serializers.IntegerField()
    scheduled_occurrences = serializers.IntegerField()
    completed_occurrences = serializers.IntegerField()
    cancelled_occurrences = serializers.IntegerField()
    available_capacity = serializers.IntegerField()
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    booked_slots = serializers.IntegerField()
    attended_slots = serializers.IntegerField()
    no_show_slots = serializers.IntegerField()
    cancelled_slots = serializers.IntegerField()
    reserved_slots = serializers.IntegerField()
    remaining_capacity = serializers.IntegerField()


class ClassReportTotalsSerializer(serializers.Serializer):
    occurrences = serializers.IntegerField()
    scheduled_occurrences = serializers.IntegerField()
    completed_occurrences = serializers.IntegerField()
    cancelled_occurrences = serializers.IntegerField()
    available_capacity = serializers.IntegerField()
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    unique_trainers = serializers.IntegerField()
    booked_slots = serializers.IntegerField()
    attended_slots = serializers.IntegerField()
    no_show_slots = serializers.IntegerField()
    cancelled_slots = serializers.IntegerField()
    reserved_slots = serializers.IntegerField()
    remaining_capacity = serializers.IntegerField()


class ClassReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = ClassReportDaySerializer(many=True)
    totals = ClassReportTotalsSerializer()


class ClassReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassReportSerializer()
    message = serializers.CharField()


class ReportCursorMetaSerializer(serializers.Serializer):
    next_after = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class ProductSalesReportRowSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    sku = serializers.CharField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()
    quantity_sold = serializers.CharField()
    revenue = serializers.CharField()
    cogs = serializers.CharField()
    gross_profit = serializers.CharField()


class ProductSalesReportTotalsSerializer(serializers.Serializer):
    quantity_sold = serializers.CharField()
    revenue = serializers.CharField()
    cogs = serializers.CharField()
    gross_profit = serializers.CharField()
    product_count = serializers.IntegerField()
    active_product_count = serializers.IntegerField()


class ProductSalesReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = ProductSalesReportRowSerializer(many=True)
    totals = ProductSalesReportTotalsSerializer()
    meta = ReportCursorMetaSerializer()


class ProductSalesReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProductSalesReportSerializer()
    message = serializers.CharField()


class StockMovementReportRowSerializer(serializers.Serializer):
    date = serializers.DateField()
    opening_quantity = serializers.CharField()
    purchase_quantity = serializers.CharField()
    sale_quantity = serializers.CharField()
    sale_return_quantity = serializers.CharField()
    damage_quantity = serializers.CharField()
    loss_quantity = serializers.CharField()
    adjustment_quantity = serializers.CharField()
    correction_quantity = serializers.CharField()
    net_quantity = serializers.CharField()


class StockMovementReportTotalsSerializer(serializers.Serializer):
    opening_quantity = serializers.CharField()
    purchase_quantity = serializers.CharField()
    sale_quantity = serializers.CharField()
    sale_return_quantity = serializers.CharField()
    damage_quantity = serializers.CharField()
    loss_quantity = serializers.CharField()
    adjustment_quantity = serializers.CharField()
    correction_quantity = serializers.CharField()
    net_quantity = serializers.CharField()


class StockMovementReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = StockMovementReportRowSerializer(many=True)
    totals = StockMovementReportTotalsSerializer()


class StockMovementReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = StockMovementReportSerializer()
    message = serializers.CharField()


class InventoryReportRowSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    category_code = serializers.CharField()
    category_name = serializers.CharField()
    sku = serializers.CharField()
    name = serializers.CharField()
    unit = serializers.CharField()
    is_active = serializers.BooleanField()
    stock_quantity = serializers.CharField()
    low_stock_threshold = serializers.CharField()
    is_low_stock = serializers.BooleanField()


class InventoryReportTotalsSerializer(serializers.Serializer):
    product_count = serializers.IntegerField()
    active_product_count = serializers.IntegerField()
    low_stock_product_count = serializers.IntegerField()
    total_stock_quantity = serializers.CharField()


class InventoryReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = InventoryReportRowSerializer(many=True)
    totals = InventoryReportTotalsSerializer()
    meta = ReportCursorMetaSerializer()


class InventoryReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = InventoryReportSerializer()
    message = serializers.CharField()


class TrainerSessionCountsSerializer(serializers.Serializer):
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    booked_sessions = serializers.IntegerField()
    attended_sessions = serializers.IntegerField()
    no_show_sessions = serializers.IntegerField()
    cancelled_sessions = serializers.IntegerField()


class TrainerUpcomingSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    member_number = serializers.CharField()
    full_name = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()


class TrainerDashboardSerializer(serializers.Serializer):
    timezone = serializers.CharField()
    today = serializers.DateField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    trainer_staff_profile_id = serializers.UUIDField()
    active_assigned_members = serializers.IntegerField()
    sessions = TrainerSessionCountsSerializer()
    upcoming_bookings = TrainerUpcomingSessionSerializer(many=True)


class TrainerDashboardEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TrainerDashboardSerializer()
    message = serializers.CharField()


class TrainerSessionReportDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    bookings = serializers.IntegerField()
    unique_members = serializers.IntegerField()
    booked_sessions = serializers.IntegerField()
    attended_sessions = serializers.IntegerField()
    no_show_sessions = serializers.IntegerField()
    cancelled_sessions = serializers.IntegerField()


class TrainerSessionReportSerializer(serializers.Serializer):
    gym = ReportGymSerializer()
    timezone = serializers.CharField()
    range = ReportRangeSerializer()
    generated_at = serializers.DateTimeField()
    source_status_policy = serializers.CharField()
    rows = TrainerSessionReportDaySerializer(many=True)
    totals = TrainerSessionCountsSerializer()


class TrainerSessionReportEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TrainerSessionReportSerializer()
    message = serializers.CharField()

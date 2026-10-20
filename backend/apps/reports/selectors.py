"""Gym-scoped tenant dashboard totals.

Revenue follows the daily-sales ledger: posted and void charges count on
their business date, and posted refunds and reversals subtract on their own
business date in the original charge's bucket. Draft, pending, rejected, and
cancelled rows are absent. Expenses follow ``expense_net_totals``: posted and
void originals count on their business date, and posted reversals subtract on
the correction date. A range that contains both dates therefore reconciles to
the finalized net. Dropping void charges while still subtracting their
reversals would make that net negative.

Revenue components are mutually exclusive. Anything that does not match
new-member, renewal, PT, class, or product falls into other, so the six
amounts add up to ``total_revenue``. Equal amounts rank by the canonical
source order. Product rows use immutable line snapshots, adding posted and
void sales on the sale date and subtracting a full void and each partial
return on the correction date. ``top_product_items`` keeps the first
``TOP_PRODUCT_LIMIT`` rows after ranking by revenue, gross profit, quantity,
and product id. A row that nets to zero does not take one of those places.

Pending payment amounts are expired membership balances only.
``MemberServicePackage.amount_snapshot`` is a catalog price, and its payment
link is not an unpaid or partial balance. ``PTSale.gross_fee`` is a sale
price without an ``UNPAID`` or ``PARTIAL`` obligation. Class grants have no
priced debt field. Those records are not invented as member debt.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Q,
    QuerySet,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Greatest, TruncDate

from apps.cashbook.models import CashSession, CashTransaction
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.expenses.models import Expense
from apps.inventory.quantities import format_quantity
from apps.members.models import CheckIn, Member, Membership, Payment
from apps.members.payment_serializers import money_text
from apps.members.services import _linked_payment_total
from apps.reports.ranges import iter_dates
from apps.salaries.models import SalaryPeriod
from apps.sales.models import RetailReturnLine, RetailSale, RetailSaleLine
from apps.tenants.models import Gym
from apps.tenants.sequence_services import EXPENSE_CATEGORY_CATALOG
from apps.trainers.models import PTBooking, PTSale, TrainerAssignment
from apps.trainers.selectors import effective_assignment_filter

_ZERO = Decimal("0.00")
_ZERO_QTY = Decimal("0.000")
_MONEY: DecimalField[Any, Any] = DecimalField(max_digits=18, decimal_places=2)
_SOURCES = ("NEW_MEMBER", "RENEWAL", "PT", "CLASS", "PRODUCT", "OTHER")
_BUCKET_FIELDS = {
    "NEW_MEMBER": "new_member_revenue",
    "RENEWAL": "renewal_revenue",
    "PT": "pt_revenue",
    "CLASS": "class_revenue",
    "PRODUCT": "product_revenue",
    "OTHER": "other_revenue",
}
SOURCE_STATUS_POLICY = {
    "revenue": (
        "posted_and_void_charges_on_business_date__posted_refunds_and_reversals_on_correction_date"
    ),
    "expenses": ("posted_and_void_originals_on_business_date__posted_reversals_on_correction_date"),
    "operations": "authoritative_rows_on_gym_local_dates",
}
PERIOD_REPORT_QUERY_COUNT = 10
CASH_BOOK_QUERY_COUNT = 2
PROFIT_LOSS_QUERY_COUNT = 6
MEMBERSHIP_EXPIRY_QUERY_COUNT = 2
CHECK_IN_QUERY_COUNT = 4
PT_SESSION_QUERY_COUNT = 2
CLASS_REPORT_QUERY_COUNT = 4
TRAINER_SESSION_REPORT_QUERY_COUNT = 2
TRAINER_DASHBOARD_QUERY_COUNT = 3
TRAINER_UPCOMING_LIMIT = 10
TRAINER_UPCOMING_DAYS = 7
MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY = (
    "expiry_date_and_current_status_at_generation__"
    "active_suspended_or_expired__"
    "not_historical_point_in_time_status"
)
PT_SESSION_SOURCE_STATUS_POLICY = (
    "gym_local_starts_at_date_and_current_status_at_generation__"
    "booked_attended_no_show_cancelled_are_mutually_exhaustive__"
    "metered_and_unmetered_include_every_status__"
    "not_status_transition_date_or_history"
)
TRAINER_SESSION_SOURCE_STATUS_POLICY = (
    "own_active_trainer_profile_only__"
    "gym_local_starts_at_date_and_current_status_at_generation__"
    "booked_attended_no_show_cancelled_are_mutually_exhaustive__"
    "not_status_transition_date_or_history"
)
CLASS_SOURCE_STATUS_POLICY = (
    "occurrence_local_date_and_current_occurrence_and_booking_status_at_generation__"
    "available_capacity_sums_scheduled_and_completed_occurrence_capacity__"
    "reserved_slots_exclude_cancelled_bookings__"
    "remaining_capacity_is_available_minus_reserved_clamped_at_zero__"
    "not_status_transition_dates"
)
_CLASS_CAPACITY_STATUSES = (
    ClassOccurrence.Status.SCHEDULED,
    ClassOccurrence.Status.COMPLETED,
)
_PT_SESSION_COUNT_FIELDS = (
    "bookings",
    "unique_members",
    "unique_trainers",
    "booked_sessions",
    "attended_sessions",
    "no_show_sessions",
    "cancelled_sessions",
    "metered_sessions",
    "unmetered_sessions",
)
_TRAINER_SESSION_COUNT_FIELDS = (
    "bookings",
    "unique_members",
    "booked_sessions",
    "attended_sessions",
    "no_show_sessions",
    "cancelled_sessions",
)
_CLASS_COUNT_FIELDS = (
    "occurrences",
    "scheduled_occurrences",
    "completed_occurrences",
    "cancelled_occurrences",
    "available_capacity",
    "bookings",
    "unique_members",
    "booked_slots",
    "attended_slots",
    "no_show_slots",
    "cancelled_slots",
    "reserved_slots",
    "remaining_capacity",
)
CHECK_IN_SOURCE_STATUS_POLICY = (
    "gym_local_check_in_at_half_open_window__"
    "methods_manual_qr_nfc_biometric_are_mutually_exhaustive__"
    "check_outs_grouped_by_gym_local_check_out_at__"
    "open_visits_from_day_are_that_local_check_in_date_still_null_at_generation__"
    "currently_checked_in_is_all_same_gym_null_checkouts_independent_of_selected_range"
)
_MEMBERSHIP_EXPIRY_STATUSES = (
    Membership.Status.ACTIVE,
    Membership.Status.SUSPENDED,
    Membership.Status.EXPIRED,
)
_MEMBERSHIP_COUNT_FIELDS = (
    "expiring_memberships",
    "unique_members",
    "active_memberships",
    "suspended_memberships",
    "expired_memberships",
)
_CHECK_IN_COUNT_FIELDS = (
    "check_ins",
    "unique_attendees",
    "manual_check_ins",
    "qr_check_ins",
    "nfc_check_ins",
    "biometric_check_ins",
    "check_outs",
    "open_visits_from_day",
)
CASH_BOOK_SOURCE_STATUS_POLICY = {
    "sessions": "all_sessions_on_business_date__open_closed_and_void_counted_separately",
    "transactions": "append_only_positive_amounts_grouped_by_source_type_and_direction",
}
_CASH_COUNTS = (
    "session_count",
    "open_session_count",
    "closed_session_count",
    "void_session_count",
)
_CASH_FLOWS = (
    ("PAYMENT", "IN", "payment_in"),
    ("PAYMENT", "OUT", "payment_out"),
    ("EXPENSE", "IN", "expense_in"),
    ("EXPENSE", "OUT", "expense_out"),
    ("COMMISSION", "IN", "commission_in"),
    ("COMMISSION", "OUT", "commission_out"),
    ("MANUAL", "IN", "manual_in"),
    ("MANUAL", "OUT", "manual_out"),
    ("REVERSAL", "IN", "reversal_in"),
    ("REVERSAL", "OUT", "reversal_out"),
)
_CASH_FLOW_FIELDS = tuple(field for _source, _direction, field in _CASH_FLOWS)
_CASH_MONEY = (
    "opening_cash",
    "cash_in",
    "cash_out",
    "net_cash_flow",
    *_CASH_FLOW_FIELDS,
    "expected_closing",
    "actual_closing",
    "difference",
)
_PROFIT_MONEY = (
    "new_member_revenue",
    "renewal_revenue",
    "pt_revenue",
    "class_revenue",
    "product_revenue",
    "other_revenue",
    "total_revenue",
    "product_cogs",
    "product_gross_profit",
    "total_expenses",
    "net_profit",
)
_TERMINAL_SESSION = Q(status__in=[CashSession.Status.CLOSED, CashSession.Status.VOID])
TOP_PRODUCT_LIMIT = 10
_CATEGORY_KEYS = tuple(code for code, _name in EXPENSE_CATEGORY_CATALOG)
_OPEN_MEMBERSHIP = (Membership.Status.ACTIVE, Membership.Status.SUSPENDED)


class DashboardRange(TypedDict):
    date_from: str
    date_to: str


class OperationalMetrics(TypedDict):
    active_members: int
    new_members: int
    expired_memberships: int
    check_ins: int
    unique_attendees: int
    currently_checked_in: int
    expiring_within_3_days: int
    expiring_within_7_days: int
    expiring_within_30_days: int
    pending_approvals: int


class RevenueRank(TypedDict):
    source: str
    amount: str


class ProductRank(TypedDict):
    product_id: UUID
    name: str
    quantity_sold: str
    revenue: str
    gross_profit: str


class ExpenseRank(TypedDict):
    system_key: str
    amount: str


class FinancialMetrics(TypedDict):
    total_revenue: str
    new_member_revenue: str
    renewal_revenue: str
    pt_revenue: str
    class_revenue: str
    product_revenue: str
    other_revenue: str
    pending_payment_member_count: int
    pending_payment_amount: str
    revenue_sources_ranked: list[RevenueRank]
    top_product_items: list[ProductRank]
    total_expenses: str
    expenses_by_category: list[ExpenseRank]
    staff_salary_expense: str
    pt_commission_expense: str
    net_profit: str


class DashboardPayload(TypedDict):
    range: DashboardRange
    timezone: str
    currency: str
    operational: OperationalMetrics
    financial: FinancialMetrics | None


def tenant_dashboard(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    today: date,
    include_financial: bool,
) -> DashboardPayload:
    """Return operational totals and, when permitted, the financial block."""

    financial = (
        _financial(gym_id=gym.id, date_from=date_from, date_to=date_to, today=today)
        if include_financial
        else None
    )
    return {
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "timezone": gym.timezone,
        "currency": gym.currency,
        "operational": _operational(
            gym_id=gym.id,
            timezone_name=gym.timezone,
            date_from=date_from,
            date_to=date_to,
            today=today,
        ),
        "financial": financial,
    }


def period_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one ascending row per gym-local date and the range totals.

    Revenue, expenses, and product lines use the same correction-date rules as
    the dashboard. The ten queries are grouped by date, so a 1-day report and
    a 31-day report run the same number of queries.
    """

    dates = iter_dates(date_from, date_to)
    revenue = _revenue_by_date(gym_id=gym.id, date_from=date_from, date_to=date_to)
    expenses = _expense_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    products = _product_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    operations = _operation_days(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    unique = _range_unique_attendees(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    rows = [_report_row(day, revenue, expenses, products, operations) for day in dates]
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "currency": gym.currency,
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated_at": generated_at,
        "source_status_policy": SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": _report_totals(rows, expenses, unique),
    }


def cash_book_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one cash row per date from sessions and append-only movements.

    Sessions and transactions are each one grouped query. A 1-day report and a
    366-day report therefore run the same two queries. Amounts stay positive;
    direction is the only sign. ``VOID`` is counted on its own and is neither
    open nor closed. Actual closing and difference sum terminal snapshots only,
    and are ``0.00`` when the date has none.
    """

    dates = iter_dates(date_from, date_to)
    sessions = _cash_session_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    flows = _cash_flow_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    rows = [_cash_row(day, sessions, flows) for day in dates]
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "currency": gym.currency,
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated_at": generated_at,
        "source_status_policy": CASH_BOOK_SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": _summed_report(rows, _CASH_COUNTS, _CASH_MONEY),
    }


def profit_loss_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one profit row per date using the period-report formulas.

    Revenue, expenses, and product snapshots are the same correction-date
    helpers as the dashboard and the daily and monthly reports. Net profit
    remains total revenue minus total expenses. Product COGS is reported beside
    that result and is not subtracted again. The six financial queries are
    grouped by date, so the count does not grow with the range.
    """

    dates = iter_dates(date_from, date_to)
    revenue = _revenue_by_date(gym_id=gym.id, date_from=date_from, date_to=date_to)
    expenses = _expense_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    products = _product_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    rows = [_profit_row(day, revenue, expenses, products) for day in dates]
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "currency": gym.currency,
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated_at": generated_at,
        "source_status_policy": SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": _profit_totals(rows, expenses),
    }


def membership_expiry_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one expiry row per date from the membership's current status.

    Counts use ``expiry_date`` and the status stored at generation. Pending and
    cancelled rows are excluded. Daily ``unique_members`` is distinct that day.
    The range total is distinct across the whole range, so it is not the sum of
    the daily uniques. The grouped day query and that range count are the only
    two queries for 1 or 366 days.
    """

    grouped = _membership_expiry_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    range_unique = _membership_expiry_people(gym_id=gym.id, date_from=date_from, date_to=date_to)
    rows = [
        _count_row(day, grouped, _MEMBERSHIP_COUNT_FIELDS) for day in iter_dates(date_from, date_to)
    ]
    totals = _summed_counts(rows, _MEMBERSHIP_COUNT_FIELDS)
    totals["unique_members"] = range_unique
    return _operational_report(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        source_status_policy=MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY,
        rows=rows,
        totals=totals,
    )


def check_in_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one attendance row per gym-local date.

    Check-ins and check-outs use a half-open local window, so a UTC instant can
    fall on the previous or next gym date across a timezone offset or DST
    change. Method counts cover manual, QR, NFC, and biometric. Check-outs
    follow the local ``check_out_at`` date even when the check-in was outside
    the range. ``open_visits_from_day`` counts that local check-in date when
    checkout is still null. ``currently_checked_in`` counts every same-gym open
    visit at generation and ignores the selected range. The four queries are
    grouped check-ins, grouped check-outs, range-distinct attendees, and
    current occupancy.
    """

    grouped = _check_in_days(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    range_unique = _check_in_attendees(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    occupancy = _currently_checked_in(gym_id=gym.id)
    rows = [
        _count_row(day, grouped, _CHECK_IN_COUNT_FIELDS) for day in iter_dates(date_from, date_to)
    ]
    totals = _summed_counts(rows, _CHECK_IN_COUNT_FIELDS)
    totals["unique_attendees"] = range_unique
    totals["currently_checked_in"] = occupancy
    return _operational_report(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        source_status_policy=CHECK_IN_SOURCE_STATUS_POLICY,
        rows=rows,
        totals=totals,
    )


def pt_session_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one PT session row per gym-local ``starts_at`` date.

    Counts use the booking's current status at generation. Booked, attended,
    no-show, and cancelled are mutually exhaustive. Metered and unmetered
    include every status. Daily member and trainer counts are distinct that
    day. Each range total is distinct across the whole range. The grouped day
    query and the range aggregate are the only two queries for 1 or 366 days.
    """

    grouped = _pt_session_days(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    range_members, range_trainers = _pt_session_people(
        gym_id=gym.id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    rows = [
        _count_row(day, grouped, _PT_SESSION_COUNT_FIELDS) for day in iter_dates(date_from, date_to)
    ]
    totals = _summed_counts(rows, _PT_SESSION_COUNT_FIELDS)
    totals["unique_members"] = range_members
    totals["unique_trainers"] = range_trainers
    return _operational_report(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        source_status_policy=PT_SESSION_SOURCE_STATUS_POLICY,
        rows=rows,
        totals=totals,
    )


def class_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one class row per occurrence ``local_date``.

    Occurrence and booking counts use the current status at generation.
    Available capacity sums capacity on scheduled and completed occurrences
    only, without joining bookings. Reserved slots are booked, attended, and
    no-show bookings. Remaining capacity is that day's available capacity
    minus reserved slots, clamped at zero, and the total sums those daily
    values. Daily ``unique_members`` is distinct that day. Range
    ``unique_members`` is distinct across bookings, and range
    ``unique_trainers`` is distinct across occurrences. The four queries are
    grouped occurrences, grouped bookings, range-distinct members, and
    range-distinct trainers.
    """

    grouped = _class_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    range_members = _class_members(gym_id=gym.id, date_from=date_from, date_to=date_to)
    range_trainers = _class_trainers(gym_id=gym.id, date_from=date_from, date_to=date_to)
    rows = [_count_row(day, grouped, _CLASS_COUNT_FIELDS) for day in iter_dates(date_from, date_to)]
    totals = _summed_counts(rows, _CLASS_COUNT_FIELDS)
    totals["unique_members"] = range_members
    totals["unique_trainers"] = range_trainers
    return _operational_report(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        source_status_policy=CLASS_SOURCE_STATUS_POLICY,
        rows=rows,
        totals=totals,
    )


def trainer_dashboard(
    *,
    gym: Gym,
    trainer_staff_profile_id: UUID,
    date_from: date,
    date_to: date,
    today: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return this trainer's assignment, range, and upcoming session reads.

    ``generated_at`` is the instant captured by the view. It is both the
    response timestamp and the upcoming lower bound; this selector does not
    read the clock. ``active_assigned_members`` counts this profile's ACTIVE
    assignments whose effective dates contain gym-local today. The selected
    range does not move that count. Session counts use the booking's current
    status on the gym-local ``starts_at`` date. Booked, attended, no-show, and
    cancelled are mutually exhaustive, and ``unique_members`` is distinct
    across the range. Upcoming rows are BOOKED sessions at or after
    ``generated_at`` and before gym-local midnight seven days after today, so
    today is the first of those seven days. They are ordered by ``starts_at``,
    ``id`` and stop at 10. The three queries are the assignment count, the
    range aggregate, and the upcoming list, for a 1-day or 366-day range. The
    response has no money or contact fields and no other trainer's rows.
    """

    zone = ZoneInfo(gym.timezone)
    start, end = _local_window(date_from, date_to, zone)
    assignments = (
        TrainerAssignment.objects.filter(
            gym_id=gym.id,
            trainer_staff_profile_id=trainer_staff_profile_id,
            member__gym_id=gym.id,
        )
        .filter(effective_assignment_filter(as_of=today))
        .aggregate(active_assigned_members=Count("id"))
    )
    counted = (
        _own_pt_bookings(gym_id=gym.id, trainer_staff_profile_id=trainer_staff_profile_id)
        .filter(starts_at__gte=start, starts_at__lt=end)
        .aggregate(**_trainer_session_count_annotations())
    )
    upcoming_start, upcoming_end = _upcoming_bounds(today, zone, generated_at)
    upcoming = (
        _own_pt_bookings(gym_id=gym.id, trainer_staff_profile_id=trainer_staff_profile_id)
        .filter(
            status=PTBooking.Status.BOOKED,
            starts_at__gte=upcoming_start,
            starts_at__lt=upcoming_end,
        )
        .order_by("starts_at", "id")
        .values(
            "id",
            "member_id",
            "member__member_number",
            "member__first_name",
            "member__last_name",
            "starts_at",
            "ends_at",
        )[:TRAINER_UPCOMING_LIMIT]
    )
    return {
        "timezone": gym.timezone,
        "today": today.isoformat(),
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated_at": generated_at,
        "trainer_staff_profile_id": trainer_staff_profile_id,
        "active_assigned_members": _count(assignments["active_assigned_members"]),
        "sessions": {field: _count(counted.get(field)) for field in _TRAINER_SESSION_COUNT_FIELDS},
        "upcoming_bookings": [_upcoming_row(row) for row in upcoming],
    }


def trainer_session_report(
    *,
    gym: Gym,
    trainer_staff_profile_id: UUID,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return one own-session row per gym-local ``starts_at`` date.

    Counts use the booking's current status at generation. Booked, attended,
    no-show, and cancelled are mutually exhaustive. Daily ``unique_members``
    is distinct that day. The range total is distinct across the whole range.
    Missing days are zero. The grouped day query and the range member
    aggregate are the only two queries for 1 or 366 days. The response has no
    money or contact fields.
    """

    grouped = _trainer_session_days(
        gym_id=gym.id,
        trainer_staff_profile_id=trainer_staff_profile_id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    range_members = _trainer_session_members(
        gym_id=gym.id,
        trainer_staff_profile_id=trainer_staff_profile_id,
        timezone_name=gym.timezone,
        date_from=date_from,
        date_to=date_to,
    )
    rows = [
        _count_row(day, grouped, _TRAINER_SESSION_COUNT_FIELDS)
        for day in iter_dates(date_from, date_to)
    ]
    totals = _summed_counts(rows, _TRAINER_SESSION_COUNT_FIELDS)
    totals["unique_members"] = range_members
    return _operational_report(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        source_status_policy=TRAINER_SESSION_SOURCE_STATUS_POLICY,
        rows=rows,
        totals=totals,
    )


def _remaining_capacity(available: int, reserved: int) -> int:
    return max(available - reserved, 0)


def _operational_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
    source_status_policy: str,
    rows: list[dict[str, object]],
    totals: dict[str, int],
) -> dict[str, object]:
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated_at": generated_at,
        "source_status_policy": source_status_policy,
        "rows": rows,
        "totals": totals,
    }


def _membership_expiry_days(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, int]]:
    queryset = (
        Membership.objects.filter(
            gym_id=gym_id,
            member__gym_id=gym_id,
            expiry_date__gte=date_from,
            expiry_date__lte=date_to,
            status__in=_MEMBERSHIP_EXPIRY_STATUSES,
        )
        .order_by()
        .values("expiry_date")
        .annotate(
            expiring_memberships=Count("id"),
            unique_members=Count("member_id", distinct=True),
            active_memberships=Count("id", filter=Q(status=Membership.Status.ACTIVE)),
            suspended_memberships=Count("id", filter=Q(status=Membership.Status.SUSPENDED)),
            expired_memberships=Count("id", filter=Q(status=Membership.Status.EXPIRED)),
        )
    )
    return _grouped_counts(queryset, "expiry_date", _MEMBERSHIP_COUNT_FIELDS)


def _membership_expiry_people(*, gym_id: UUID, date_from: date, date_to: date) -> int:
    stats = Membership.objects.filter(
        gym_id=gym_id,
        member__gym_id=gym_id,
        expiry_date__gte=date_from,
        expiry_date__lte=date_to,
        status__in=_MEMBERSHIP_EXPIRY_STATUSES,
    ).aggregate(unique_members=Count("member_id", distinct=True))
    return _count(stats["unique_members"])


def _check_in_days(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, int]]:
    zone = ZoneInfo(timezone_name)
    start, end = _local_window(date_from, date_to, zone)
    arrivals = (
        CheckIn.objects.filter(
            gym_id=gym_id,
            member__gym_id=gym_id,
            check_in_at__gte=start,
            check_in_at__lt=end,
        )
        .annotate(local_date=TruncDate("check_in_at", tzinfo=zone))
        .order_by()
        .values("local_date")
        .annotate(
            check_ins=Count("id"),
            unique_attendees=Count("member_id", distinct=True),
            manual_check_ins=Count("id", filter=Q(method=CheckIn.Method.MANUAL)),
            qr_check_ins=Count("id", filter=Q(method=CheckIn.Method.QR)),
            nfc_check_ins=Count("id", filter=Q(method=CheckIn.Method.NFC)),
            biometric_check_ins=Count("id", filter=Q(method=CheckIn.Method.BIOMETRIC)),
            open_visits_from_day=Count("id", filter=Q(check_out_at__isnull=True)),
        )
    )
    departures = (
        CheckIn.objects.filter(
            gym_id=gym_id,
            member__gym_id=gym_id,
            check_out_at__gte=start,
            check_out_at__lt=end,
        )
        .annotate(local_date=TruncDate("check_out_at", tzinfo=zone))
        .order_by()
        .values("local_date")
        .annotate(check_outs=Count("id"))
    )
    days = _grouped_counts(arrivals, "local_date", _CHECK_IN_COUNT_FIELDS)
    for local_date, counts in _grouped_counts(departures, "local_date", ("check_outs",)).items():
        days.setdefault(local_date, {key: 0 for key in _CHECK_IN_COUNT_FIELDS})
        days[local_date]["check_outs"] = counts["check_outs"]
    return days


def _check_in_attendees(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> int:
    start, end = _local_window(date_from, date_to, ZoneInfo(timezone_name))
    stats = CheckIn.objects.filter(
        gym_id=gym_id,
        member__gym_id=gym_id,
        check_in_at__gte=start,
        check_in_at__lt=end,
    ).aggregate(unique_attendees=Count("member_id", distinct=True))
    return _count(stats["unique_attendees"])


def _currently_checked_in(*, gym_id: UUID) -> int:
    stats = CheckIn.objects.filter(
        gym_id=gym_id,
        member__gym_id=gym_id,
        check_out_at__isnull=True,
    ).aggregate(currently_checked_in=Count("id"))
    return _count(stats["currently_checked_in"])


def _pt_session_days(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, int]]:
    zone = ZoneInfo(timezone_name)
    start, end = _local_window(date_from, date_to, zone)
    queryset = (
        PTBooking.objects.filter(
            gym_id=gym_id,
            member__gym_id=gym_id,
            trainer_staff_profile__gym_id=gym_id,
            starts_at__gte=start,
            starts_at__lt=end,
        )
        .annotate(local_date=TruncDate("starts_at", tzinfo=zone))
        .order_by()
        .values("local_date")
        .annotate(
            bookings=Count("id"),
            unique_members=Count("member_id", distinct=True),
            unique_trainers=Count("trainer_staff_profile_id", distinct=True),
            booked_sessions=Count("id", filter=Q(status=PTBooking.Status.BOOKED)),
            attended_sessions=Count("id", filter=Q(status=PTBooking.Status.ATTENDED)),
            no_show_sessions=Count("id", filter=Q(status=PTBooking.Status.NO_SHOW)),
            cancelled_sessions=Count("id", filter=Q(status=PTBooking.Status.CANCELLED)),
            metered_sessions=Count("id", filter=Q(unmetered=False)),
            unmetered_sessions=Count("id", filter=Q(unmetered=True)),
        )
    )
    return _grouped_counts(queryset, "local_date", _PT_SESSION_COUNT_FIELDS)


def _pt_session_people(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> tuple[int, int]:
    start, end = _local_window(date_from, date_to, ZoneInfo(timezone_name))
    stats = PTBooking.objects.filter(
        gym_id=gym_id,
        member__gym_id=gym_id,
        trainer_staff_profile__gym_id=gym_id,
        starts_at__gte=start,
        starts_at__lt=end,
    ).aggregate(
        unique_members=Count("member_id", distinct=True),
        unique_trainers=Count("trainer_staff_profile_id", distinct=True),
    )
    return _count(stats["unique_members"]), _count(stats["unique_trainers"])


def _trainer_session_count_annotations() -> dict[str, Any]:
    return {
        "bookings": Count("id"),
        "unique_members": Count("member_id", distinct=True),
        "booked_sessions": Count("id", filter=Q(status=PTBooking.Status.BOOKED)),
        "attended_sessions": Count("id", filter=Q(status=PTBooking.Status.ATTENDED)),
        "no_show_sessions": Count("id", filter=Q(status=PTBooking.Status.NO_SHOW)),
        "cancelled_sessions": Count("id", filter=Q(status=PTBooking.Status.CANCELLED)),
    }


def _own_pt_bookings(*, gym_id: UUID, trainer_staff_profile_id: UUID) -> QuerySet[PTBooking]:
    return PTBooking.objects.filter(
        gym_id=gym_id,
        trainer_staff_profile_id=trainer_staff_profile_id,
        trainer_staff_profile__gym_id=gym_id,
        member__gym_id=gym_id,
    )


def _upcoming_bounds(
    today: date,
    zone: ZoneInfo,
    generated_at: datetime,
) -> tuple[datetime, datetime]:
    """Return ``[generated_at, local midnight seven days after today)``.

    ``generated_at`` is the view's captured instant. Today is the first
    gym-local day. A session that starts before that instant is outside the
    window. The last included gym-local date is six days after today.
    """

    return (
        generated_at,
        datetime.combine(today + timedelta(days=TRAINER_UPCOMING_DAYS), time.min, tzinfo=zone),
    )


def _member_full_name(first_name: object, last_name: object) -> str:
    first = first_name if isinstance(first_name, str) else ""
    last = last_name if isinstance(last_name, str) else ""
    return f"{first} {last}".strip()


class _UpcomingValues(TypedDict):
    id: UUID
    member_id: UUID
    member__member_number: str
    member__first_name: str
    member__last_name: str
    starts_at: datetime
    ends_at: datetime


def _upcoming_row(row: _UpcomingValues) -> dict[str, object]:
    return {
        "id": row["id"],
        "member_id": row["member_id"],
        "member_number": row["member__member_number"],
        "full_name": _member_full_name(row["member__first_name"], row["member__last_name"]),
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
    }


def _trainer_session_days(
    *,
    gym_id: UUID,
    trainer_staff_profile_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, int]]:
    zone = ZoneInfo(timezone_name)
    start, end = _local_window(date_from, date_to, zone)
    queryset = (
        _own_pt_bookings(gym_id=gym_id, trainer_staff_profile_id=trainer_staff_profile_id)
        .filter(starts_at__gte=start, starts_at__lt=end)
        .annotate(local_date=TruncDate("starts_at", tzinfo=zone))
        .order_by()
        .values("local_date")
        .annotate(**_trainer_session_count_annotations())
    )
    return _grouped_counts(queryset, "local_date", _TRAINER_SESSION_COUNT_FIELDS)


def _trainer_session_members(
    *,
    gym_id: UUID,
    trainer_staff_profile_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> int:
    start, end = _local_window(date_from, date_to, ZoneInfo(timezone_name))
    stats = (
        _own_pt_bookings(gym_id=gym_id, trainer_staff_profile_id=trainer_staff_profile_id)
        .filter(starts_at__gte=start, starts_at__lt=end)
        .aggregate(unique_members=Count("member_id", distinct=True))
    )
    return _count(stats["unique_members"])


def _class_days(*, gym_id: UUID, date_from: date, date_to: date) -> dict[date, dict[str, int]]:
    occurrences = (
        ClassOccurrence.objects.filter(
            gym_id=gym_id,
            trainer_staff_profile__gym_id=gym_id,
            local_date__gte=date_from,
            local_date__lte=date_to,
        )
        .order_by()
        .values("local_date")
        .annotate(
            occurrences=Count("id"),
            scheduled_occurrences=Count("id", filter=Q(status=ClassOccurrence.Status.SCHEDULED)),
            completed_occurrences=Count("id", filter=Q(status=ClassOccurrence.Status.COMPLETED)),
            cancelled_occurrences=Count("id", filter=Q(status=ClassOccurrence.Status.CANCELLED)),
            available_capacity=Coalesce(
                Sum(
                    "capacity",
                    filter=Q(status__in=_CLASS_CAPACITY_STATUSES),
                    output_field=IntegerField(),
                ),
                Value(0),
            ),
        )
    )
    bookings = (
        ClassBooking.objects.filter(
            gym_id=gym_id,
            class_occurrence__gym_id=gym_id,
            member__gym_id=gym_id,
            class_occurrence__local_date__gte=date_from,
            class_occurrence__local_date__lte=date_to,
        )
        .order_by()
        .values("class_occurrence__local_date")
        .annotate(
            bookings=Count("id"),
            unique_members=Count("member_id", distinct=True),
            booked_slots=Count("id", filter=Q(status=ClassBooking.Status.BOOKED)),
            attended_slots=Count("id", filter=Q(status=ClassBooking.Status.ATTENDED)),
            no_show_slots=Count("id", filter=Q(status=ClassBooking.Status.NO_SHOW)),
            cancelled_slots=Count("id", filter=Q(status=ClassBooking.Status.CANCELLED)),
        )
    )
    occurrence_days = _grouped_counts(
        occurrences,
        "local_date",
        (
            "occurrences",
            "scheduled_occurrences",
            "completed_occurrences",
            "cancelled_occurrences",
            "available_capacity",
        ),
    )
    booking_days = _grouped_counts(
        bookings,
        "class_occurrence__local_date",
        (
            "bookings",
            "unique_members",
            "booked_slots",
            "attended_slots",
            "no_show_slots",
            "cancelled_slots",
        ),
    )
    grouped: dict[date, dict[str, int]] = {}
    for local_date in set(occurrence_days) | set(booking_days):
        counts = {field: 0 for field in _CLASS_COUNT_FIELDS}
        counts.update(occurrence_days.get(local_date, {}))
        counts.update(booking_days.get(local_date, {}))
        reserved = counts["booked_slots"] + counts["attended_slots"] + counts["no_show_slots"]
        counts["reserved_slots"] = reserved
        counts["remaining_capacity"] = _remaining_capacity(counts["available_capacity"], reserved)
        grouped[local_date] = counts
    return grouped


def _class_members(*, gym_id: UUID, date_from: date, date_to: date) -> int:
    stats = ClassBooking.objects.filter(
        gym_id=gym_id,
        class_occurrence__gym_id=gym_id,
        member__gym_id=gym_id,
        class_occurrence__local_date__gte=date_from,
        class_occurrence__local_date__lte=date_to,
    ).aggregate(unique_members=Count("member_id", distinct=True))
    return _count(stats["unique_members"])


def _class_trainers(*, gym_id: UUID, date_from: date, date_to: date) -> int:
    stats = ClassOccurrence.objects.filter(
        gym_id=gym_id,
        trainer_staff_profile__gym_id=gym_id,
        local_date__gte=date_from,
        local_date__lte=date_to,
    ).aggregate(unique_trainers=Count("trainer_staff_profile_id", distinct=True))
    return _count(stats["unique_trainers"])


def _grouped_counts(
    queryset: QuerySet[Any, Any],
    date_key: str,
    fields: tuple[str, ...],
) -> dict[date, dict[str, int]]:
    grouped: dict[date, dict[str, int]] = {}
    for row in queryset:
        local_date = _row_date(row, date_key)
        if local_date is None:
            continue
        grouped[local_date] = {field: _count(row.get(field)) for field in fields}
    return grouped


def _count_row(
    day: date,
    grouped: dict[date, dict[str, int]],
    fields: tuple[str, ...],
) -> dict[str, object]:
    counts = grouped.get(day, {field: 0 for field in fields})
    return {"date": day.isoformat(), **{field: counts.get(field, 0) for field in fields}}


def _summed_counts(rows: list[dict[str, object]], fields: tuple[str, ...]) -> dict[str, int]:
    totals = {field: 0 for field in fields}
    for row in rows:
        for field in fields:
            totals[field] += _count(row[field])
    return totals


def _report_row(
    day: date,
    revenue: dict[date, dict[str, Decimal]],
    expenses: dict[date, _ExpenseDay],
    products: dict[date, _ProductMetric],
    operations: dict[date, _OperationDay],
) -> dict[str, object]:
    amounts = revenue.get(day, {source: _ZERO for source in _SOURCES})
    expense = expenses.get(day)
    product = products.get(day, _ProductMetric())
    operation = operations.get(day, _OperationDay())
    total_revenue = _money(sum((amounts[source] for source in _SOURCES), _ZERO))
    total_expenses = _ZERO if expense is None else expense.total
    return {
        "date": day.isoformat(),
        "new_members": operation.new_members,
        "expired_memberships": operation.expired_memberships,
        "check_ins": operation.check_ins,
        "unique_attendees": operation.unique_attendees,
        **{field: money_text(amounts[source]) for source, field in _BUCKET_FIELDS.items()},
        "total_revenue": money_text(total_revenue),
        "product_quantity": format_quantity(product.quantity),
        "product_cogs": money_text(product.cogs),
        "product_gross_profit": money_text(product.profit),
        "total_expenses": money_text(total_expenses),
        "net_profit": money_text(total_revenue - total_expenses),
    }


def _report_totals(
    rows: list[dict[str, object]],
    expenses: dict[date, _ExpenseDay],
    unique_attendees: int,
) -> dict[str, object]:
    money_fields = (
        "new_member_revenue",
        "renewal_revenue",
        "pt_revenue",
        "class_revenue",
        "product_revenue",
        "other_revenue",
        "total_revenue",
        "product_cogs",
        "product_gross_profit",
        "total_expenses",
        "net_profit",
    )
    counts = {"new_members": 0, "expired_memberships": 0, "check_ins": 0}
    summed = {field: _ZERO for field in money_fields}
    quantity = _ZERO_QTY
    for row in rows:
        for key in counts:
            counts[key] += _count(row[key])
        for field in money_fields:
            summed[field] += _parse_money(row[field])
        quantity += _quantity_text(row["product_quantity"])
    categories = {key: _ZERO for key in _CATEGORY_KEYS}
    for day in expenses.values():
        for key in _CATEGORY_KEYS:
            categories[key] += day.category(key)
    return {
        **counts,
        "unique_attendees": unique_attendees,
        **{field: money_text(summed[field]) for field in money_fields},
        "product_quantity": format_quantity(quantity),
        "staff_salary_expense": money_text(categories["STAFF_SALARY"]),
        "pt_commission_expense": money_text(categories["PT_COMMISSION"]),
        "expenses_by_category": [
            {"system_key": key, "amount": money_text(categories[key])} for key in _CATEGORY_KEYS
        ],
    }


def _profit_row(
    day: date,
    revenue: dict[date, dict[str, Decimal]],
    expenses: dict[date, _ExpenseDay],
    products: dict[date, _ProductMetric],
) -> dict[str, object]:
    amounts = revenue.get(day, {source: _ZERO for source in _SOURCES})
    expense = expenses.get(day)
    product = products.get(day, _ProductMetric())
    total_revenue = _money(sum((amounts[source] for source in _SOURCES), _ZERO))
    total_expenses = _ZERO if expense is None else expense.total
    return {
        "date": day.isoformat(),
        **{field: money_text(amounts[source]) for source, field in _BUCKET_FIELDS.items()},
        "total_revenue": money_text(total_revenue),
        "product_cogs": money_text(product.cogs),
        "product_gross_profit": money_text(product.profit),
        "total_expenses": money_text(total_expenses),
        "net_profit": money_text(total_revenue - total_expenses),
    }


def _profit_totals(
    rows: list[dict[str, object]],
    expenses: dict[date, _ExpenseDay],
) -> dict[str, object]:
    totals = _summed_report(rows, (), _PROFIT_MONEY)
    categories = {key: _ZERO for key in _CATEGORY_KEYS}
    for day in expenses.values():
        for key in _CATEGORY_KEYS:
            categories[key] += day.category(key)
    totals["staff_salary_expense"] = money_text(categories["STAFF_SALARY"])
    totals["pt_commission_expense"] = money_text(categories["PT_COMMISSION"])
    totals["expenses_by_category"] = [
        {"system_key": key, "amount": money_text(categories[key])} for key in _CATEGORY_KEYS
    ]
    return totals


def _cash_session_days(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, object]]:
    grouped = (
        CashSession.objects.filter(
            gym_id=gym_id,
            business_date__gte=date_from,
            business_date__lte=date_to,
        )
        .order_by()
        .values("business_date")
        .annotate(
            session_count=Count("id"),
            open_session_count=Count("id", filter=Q(status=CashSession.Status.OPEN)),
            closed_session_count=Count("id", filter=Q(status=CashSession.Status.CLOSED)),
            void_session_count=Count("id", filter=Q(status=CashSession.Status.VOID)),
            opening_cash=Coalesce(Sum("opening_cash"), _ZERO, output_field=_MONEY),
            expected_closing=Coalesce(Sum("expected_closing"), _ZERO, output_field=_MONEY),
            actual_closing=Coalesce(
                Sum("actual_closing", filter=_TERMINAL_SESSION),
                _ZERO,
                output_field=_MONEY,
            ),
            difference=Coalesce(
                Sum("difference", filter=_TERMINAL_SESSION),
                _ZERO,
                output_field=_MONEY,
            ),
        )
    )
    result: dict[date, dict[str, object]] = {}
    for row in grouped:
        business_date = _row_date(row, "business_date")
        if business_date is not None:
            result[business_date] = row
    return result


def _cash_flow_days(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, Decimal]]:
    grouped = (
        CashTransaction.objects.filter(
            gym_id=gym_id,
            cash_session__gym_id=gym_id,
            business_date__gte=date_from,
            business_date__lte=date_to,
        )
        .order_by()
        .values("business_date", "source_type", "direction")
        .annotate(total=Coalesce(Sum("amount"), _ZERO, output_field=_MONEY))
    )
    result: dict[date, dict[str, Decimal]] = {}
    for row in grouped:
        business_date = _row_date(row, "business_date")
        if business_date is None:
            continue
        flows = result.setdefault(business_date, {field: _ZERO for field in _CASH_FLOW_FIELDS})
        source = str(row["source_type"])
        direction = str(row["direction"])
        amount = _money(row["total"])
        for known_source, known_direction, field in _CASH_FLOWS:
            if source == known_source and direction == known_direction:
                flows[field] += amount
    return result


def _cash_row(
    day: date,
    sessions: dict[date, dict[str, object]],
    flows: dict[date, dict[str, Decimal]],
) -> dict[str, object]:
    session = sessions.get(day, {})
    movement = flows.get(day, {field: _ZERO for field in _CASH_FLOW_FIELDS})
    named = {field: _money(movement[field]) for field in _CASH_FLOW_FIELDS}
    cash_in = _money(
        named["payment_in"]
        + named["expense_in"]
        + named["commission_in"]
        + named["manual_in"]
        + named["reversal_in"]
    )
    cash_out = _money(
        named["payment_out"]
        + named["expense_out"]
        + named["commission_out"]
        + named["manual_out"]
        + named["reversal_out"]
    )
    return {
        "date": day.isoformat(),
        "session_count": _count(session.get("session_count", 0)),
        "open_session_count": _count(session.get("open_session_count", 0)),
        "closed_session_count": _count(session.get("closed_session_count", 0)),
        "void_session_count": _count(session.get("void_session_count", 0)),
        "opening_cash": money_text(_money(session.get("opening_cash", _ZERO))),
        "cash_in": money_text(cash_in),
        "cash_out": money_text(cash_out),
        "net_cash_flow": money_text(cash_in - cash_out),
        **{field: money_text(named[field]) for field in _CASH_FLOW_FIELDS},
        "expected_closing": money_text(_money(session.get("expected_closing", _ZERO))),
        "actual_closing": money_text(_money(session.get("actual_closing", _ZERO))),
        "difference": money_text(_money(session.get("difference", _ZERO))),
    }


def _summed_report(
    rows: list[dict[str, object]],
    counts: tuple[str, ...],
    money_fields: tuple[str, ...],
) -> dict[str, object]:
    summed_counts = {key: 0 for key in counts}
    summed_money = {field: _ZERO for field in money_fields}
    for row in rows:
        for key in counts:
            summed_counts[key] += _count(row[key])
        for field in money_fields:
            summed_money[field] += _parse_money(row[field])
    return {
        **summed_counts,
        **{field: money_text(summed_money[field]) for field in money_fields},
    }


def _operational(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
    today: date,
) -> OperationalMetrics:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    visits = CheckIn.objects.filter(
        gym_id=gym_id,
        check_in_at__gte=start,
        check_in_at__lt=end,
    ).aggregate(visits=Count("id"), attendees=Count("member_id", distinct=True))
    eligible = Membership.objects.filter(
        gym_id=gym_id,
        status=Membership.Status.ACTIVE,
        start_date__lte=date_to,
        expiry_date__gte=date_to,
    ).values("member_id")
    return {
        "active_members": Member.objects.filter(
            gym_id=gym_id,
            status=Member.Status.ACTIVE,
            id__in=eligible,
        ).count(),
        "new_members": Member.objects.filter(
            gym_id=gym_id,
            join_date__gte=date_from,
            join_date__lte=date_to,
        ).count(),
        "expired_memberships": Membership.objects.filter(
            gym_id=gym_id,
            expiry_date__gte=date_from,
            expiry_date__lte=date_to,
        ).count(),
        "check_ins": int(visits["visits"] or 0),
        "unique_attendees": int(visits["attendees"] or 0),
        "currently_checked_in": CheckIn.objects.filter(
            gym_id=gym_id,
            check_out_at__isnull=True,
        ).count(),
        "expiring_within_3_days": _expiring(gym_id=gym_id, today=today, days=3),
        "expiring_within_7_days": _expiring(gym_id=gym_id, today=today, days=7),
        "expiring_within_30_days": _expiring(gym_id=gym_id, today=today, days=30),
        "pending_approvals": _pending_approvals(gym_id=gym_id),
    }


def _expiring(*, gym_id: UUID, today: date, days: int) -> int:
    """Count open memberships expiring from gym-local today through N days.

    The windows are cumulative and include today. ``PENDING`` and
    ``CANCELLED`` stay out, matching the open statuses of the expiry buckets.
    The selected dashboard range does not move this clock.
    """

    return Membership.objects.filter(
        gym_id=gym_id,
        status__in=_OPEN_MEMBERSHIP,
        expiry_date__gte=today,
        expiry_date__lte=today + timedelta(days=days),
    ).count()


def _pending_approvals(*, gym_id: UUID) -> int:
    """Count current submissions. The selected range does not filter them."""

    return (
        Payment.objects.filter(gym_id=gym_id, status=Payment.Status.PENDING_APPROVAL).count()
        + Expense.objects.filter(gym_id=gym_id, status=Expense.Status.PENDING_APPROVAL).count()
        + PTSale.objects.filter(gym_id=gym_id, status=PTSale.Status.PENDING_APPROVAL).count()
        + RetailSale.objects.filter(
            gym_id=gym_id,
            status=RetailSale.Status.PENDING_APPROVAL,
        ).count()
        + SalaryPeriod.objects.filter(
            gym_id=gym_id,
            status=SalaryPeriod.Status.PENDING_APPROVAL,
        ).count()
    )


def _financial(*, gym_id: UUID, date_from: date, date_to: date, today: date) -> FinancialMetrics:
    amounts = _revenue(gym_id=gym_id, date_from=date_from, date_to=date_to)
    total_revenue = _money(sum((amounts[source] for source in _SOURCES), _ZERO))
    expenses, total_expenses = _expenses(gym_id=gym_id, date_from=date_from, date_to=date_to)
    members, pending_amount = _pending_payments(gym_id=gym_id, today=today)
    ranked = sorted(_SOURCES, key=lambda source: (-amounts[source], _SOURCES.index(source)))
    return {
        "total_revenue": money_text(total_revenue),
        "new_member_revenue": money_text(amounts["NEW_MEMBER"]),
        "renewal_revenue": money_text(amounts["RENEWAL"]),
        "pt_revenue": money_text(amounts["PT"]),
        "class_revenue": money_text(amounts["CLASS"]),
        "product_revenue": money_text(amounts["PRODUCT"]),
        "other_revenue": money_text(amounts["OTHER"]),
        "pending_payment_member_count": members,
        "pending_payment_amount": money_text(pending_amount),
        "revenue_sources_ranked": [
            {"source": source, "amount": money_text(amounts[source])} for source in ranked
        ],
        "top_product_items": _products(gym_id=gym_id, date_from=date_from, date_to=date_to),
        "total_expenses": money_text(total_expenses),
        "expenses_by_category": [
            {"system_key": key, "amount": money_text(expenses[key])} for key in _CATEGORY_KEYS
        ],
        "staff_salary_expense": money_text(expenses["STAFF_SALARY"]),
        "pt_commission_expense": money_text(expenses["PT_COMMISSION"]),
        "net_profit": money_text(total_revenue - total_expenses),
    }


def _revenue(*, gym_id: UUID, date_from: date, date_to: date) -> dict[str, Decimal]:
    combined = {source: _ZERO for source in _SOURCES}
    for amounts in _revenue_by_date(
        gym_id=gym_id,
        date_from=date_from,
        date_to=date_to,
    ).values():
        for source in _SOURCES:
            combined[source] += amounts[source]
    return {source: _money(combined[source]) for source in _SOURCES}


def _revenue_by_date(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, dict[str, Decimal]]:
    charges = Q(
        entry_type=Payment.EntryType.CHARGE,
        status__in=[Payment.Status.POSTED, Payment.Status.VOID],
    )
    returns = Q(
        entry_type__in=[Payment.EntryType.REFUND, Payment.EntryType.REVERSAL],
        status=Payment.Status.POSTED,
    )
    finalized = Payment.objects.filter(
        gym_id=gym_id,
        business_date__gte=date_from,
        business_date__lte=date_to,
    ).filter(charges | returns)
    named = ("NEW_MEMBER", "RENEWAL", "PT", "CLASS", "PRODUCT")
    annotations = {
        f"{source}_charges": _sum(charges & _bucket(source, correction=False, gym_id=gym_id))
        for source in named
    }
    annotations.update(
        {
            f"{source}_returns": _sum(returns & _bucket(source, correction=True, gym_id=gym_id))
            for source in named
        }
    )
    grouped = (
        finalized.order_by()
        .values("business_date")
        .annotate(gross=_sum(charges), returned=_sum(returns), **annotations)
    )
    result: dict[date, dict[str, Decimal]] = {}
    for row in grouped:
        business_date = _row_date(row, "business_date")
        if business_date is None:
            continue
        amounts = {
            source: _money(row[f"{source}_charges"]) - _money(row[f"{source}_returns"])
            for source in named
        }
        total = _money(row["gross"]) - _money(row["returned"])
        amounts["OTHER"] = total - sum((amounts[source] for source in named), _ZERO)
        result[business_date] = {source: _money(amounts[source]) for source in _SOURCES}
    return result


def _bucket(source: str, *, correction: bool, gym_id: UUID) -> Q:
    root = "original_payment__" if correction else ""
    scope = Q(**{f"{root}gym_id": gym_id}) if correction else Q()
    if source == "NEW_MEMBER":
        return scope & _membership_source(root, Membership.RevenueSource.NEW_MEMBER, gym_id)
    if source == "RENEWAL":
        return scope & _membership_source(root, Membership.RevenueSource.RENEWAL, gym_id)
    purpose = {
        "PT": Payment.Purpose.PT,
        "CLASS": Payment.Purpose.CLASS,
        "PRODUCT": Payment.Purpose.PRODUCT,
    }[source]
    return scope & Q(**{f"{root}purpose": purpose})


def _membership_source(root: str, revenue_source: str, gym_id: UUID) -> Q:
    return Q(
        **{
            f"{root}purpose": Payment.Purpose.MEMBERSHIP,
            f"{root}membership__gym_id": gym_id,
            f"{root}membership__revenue_source": revenue_source,
        }
    )


class _ExpenseDay:
    def __init__(self) -> None:
        self.groups: dict[str | None, Decimal] = {}

    def add(self, key: str | None, amount: Decimal, sign: int) -> None:
        self.groups[key] = self.groups.get(key, _ZERO) + (sign * amount)

    @property
    def total(self) -> Decimal:
        return _money(sum(self.groups.values(), _ZERO))

    def category(self, key: str) -> Decimal:
        return _money(self.groups.get(key, _ZERO))


def _expenses(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> tuple[dict[str, Decimal], Decimal]:
    days = _expense_days(gym_id=gym_id, date_from=date_from, date_to=date_to)
    protected = {key: _ZERO for key in _CATEGORY_KEYS}
    total = _ZERO
    for day in days.values():
        total += day.total
        for key in _CATEGORY_KEYS:
            protected[key] += day.category(key)
    return {key: _money(protected[key]) for key in _CATEGORY_KEYS}, _money(total)


def _expense_days(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, _ExpenseDay]:
    days: dict[date, _ExpenseDay] = {}
    _add_expense_days(
        days,
        Expense.objects.filter(
            gym_id=gym_id,
            status__in=[Expense.Status.POSTED, Expense.Status.VOID],
            original_expense_id__isnull=True,
            business_date__gte=date_from,
            business_date__lte=date_to,
        ),
        sign=1,
    )
    _add_expense_days(
        days,
        Expense.objects.filter(
            gym_id=gym_id,
            status=Expense.Status.POSTED,
            original_expense_id__isnull=False,
            business_date__gte=date_from,
            business_date__lte=date_to,
        ),
        sign=-1,
    )
    return days


def _add_expense_days(
    days: dict[date, _ExpenseDay],
    queryset: QuerySet[Expense],
    sign: int,
) -> None:
    grouped = (
        queryset.order_by()
        .values("business_date", "category__system_key")
        .annotate(total=Coalesce(Sum("amount"), _ZERO, output_field=_MONEY))
    )
    for row in grouped:
        business_date = _row_date(row, "business_date")
        if business_date is None:
            continue
        key = row["category__system_key"]
        category = None if key in {None, ""} else str(key)
        days.setdefault(business_date, _ExpenseDay()).add(category, _money(row["total"]), sign)


def _pending_payments(*, gym_id: UUID, today: date) -> tuple[int, Decimal]:
    """Sum expired memberships with an unpaid or partial priced balance.

    The status flag and a positive finalized balance are both required. A
    stale ``UNPAID`` row whose posted ledger already covers ``final_amount``
    contributes nothing. Package and PT prices are not balances; see the
    module note.
    """

    zero = Value(_ZERO, output_field=_MONEY)
    balance = Greatest(
        ExpressionWrapper(
            F("final_amount") - Coalesce(_linked_payment_total(), zero),
            output_field=_MONEY,
        ),
        zero,
    )
    stats = (
        Membership.objects.filter(
            gym_id=gym_id,
            expiry_date__lt=today,
            payment_status__in=[
                Membership.PaymentStatus.UNPAID,
                Membership.PaymentStatus.PARTIAL,
            ],
        )
        .annotate(balance=balance)
        .filter(balance__gt=0)
        .aggregate(
            members=Count("member_id", distinct=True),
            amount=Coalesce(Sum("balance"), zero),
        )
    )
    amount = stats["amount"]
    return int(stats["members"] or 0), _money(amount)


class _ProductTotal:
    def __init__(self) -> None:
        self.name = ""
        self.quantity = _ZERO_QTY
        self.revenue = _ZERO
        self.profit = _ZERO

    def add(
        self,
        *,
        name: str,
        quantity: Decimal,
        revenue: Decimal,
        profit: Decimal,
        sign: int,
    ) -> None:
        if name > self.name:
            self.name = name
        self.quantity += sign * quantity
        self.revenue += sign * revenue
        self.profit += sign * profit


def _products(*, gym_id: UUID, date_from: date, date_to: date) -> list[ProductRank]:
    totals: dict[UUID, _ProductTotal] = {}
    _apply_products(
        totals,
        _sale_product_rows(gym_id=gym_id, date_from=date_from, date_to=date_to, correction=False),
        sign=1,
    )
    _apply_products(
        totals,
        _sale_product_rows(gym_id=gym_id, date_from=date_from, date_to=date_to, correction=True),
        sign=-1,
    )
    _apply_products(
        totals,
        _return_product_rows(gym_id=gym_id, date_from=date_from, date_to=date_to),
        sign=-1,
    )
    ranked: list[tuple[Decimal, Decimal, Decimal, str, ProductRank]] = []
    for product_id, total in totals.items():
        quantity = total.quantity.quantize(_ZERO_QTY)
        revenue = _money(total.revenue)
        profit = _money(total.profit)
        if quantity == 0 and revenue == 0 and profit == 0:
            continue
        ranked.append(
            (
                revenue,
                profit,
                quantity,
                str(product_id),
                {
                    "product_id": product_id,
                    "name": total.name,
                    "quantity_sold": format_quantity(quantity),
                    "revenue": money_text(revenue),
                    "gross_profit": money_text(profit),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [item[4] for item in ranked[:TOP_PRODUCT_LIMIT]]


def _apply_products(
    totals: dict[UUID, _ProductTotal],
    rows: list[dict[str, object]],
    sign: int,
) -> None:
    for row in rows:
        product_id = row["product_id"]
        if not isinstance(product_id, UUID):
            continue
        bucket = totals.setdefault(product_id, _ProductTotal())
        name = row["name"]
        bucket.add(
            name=name if isinstance(name, str) else "",
            quantity=_quantity(row["quantity"]),
            revenue=_money(row["revenue"]),
            profit=_money(row["profit"]),
            sign=sign,
        )


def _sale_product_rows(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
    correction: bool,
) -> list[dict[str, object]]:
    queryset = RetailSaleLine.objects.filter(gym_id=gym_id, sale__gym_id=gym_id)
    if correction:
        queryset = queryset.filter(
            sale__status=RetailSale.Status.VOID,
            sale__void_business_date__gte=date_from,
            sale__void_business_date__lte=date_to,
        )
    else:
        queryset = queryset.filter(
            sale__status__in=[RetailSale.Status.POSTED, RetailSale.Status.VOID],
            sale__business_date__gte=date_from,
            sale__business_date__lte=date_to,
        )
    return [
        {
            "product_id": row["product_id"],
            "name": row["name"],
            "quantity": row["quantity"],
            "revenue": row["revenue"],
            "profit": row["profit"],
        }
        for row in queryset.order_by()
        .values("product_id")
        .annotate(
            quantity=Coalesce(Sum("quantity"), _ZERO_QTY),
            revenue=Coalesce(Sum("line_revenue"), _ZERO),
            profit=Coalesce(Sum("gross_profit"), _ZERO),
            name=Max("name_snapshot"),
        )
    ]


def _return_product_rows(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> list[dict[str, object]]:
    queryset = RetailReturnLine.objects.filter(
        gym_id=gym_id,
        retail_return__gym_id=gym_id,
        sale_line__gym_id=gym_id,
        retail_return__business_date__gte=date_from,
        retail_return__business_date__lte=date_to,
    )
    return [
        {
            "product_id": row["sale_line__product_id"],
            "name": row["name"],
            "quantity": row["quantity"],
            "revenue": row["revenue"],
            "profit": row["profit"],
        }
        for row in queryset.order_by()
        .values("sale_line__product_id")
        .annotate(
            quantity=Coalesce(Sum("quantity"), _ZERO_QTY),
            revenue=Coalesce(Sum("refund_revenue"), _ZERO),
            profit=Coalesce(Sum("gross_profit"), _ZERO),
            name=Max("sale_line__name_snapshot"),
        )
    ]


class _ProductMetric:
    def __init__(self) -> None:
        self.quantity = _ZERO_QTY
        self.revenue = _ZERO
        self.cogs = _ZERO
        self.profit = _ZERO

    def add(
        self,
        *,
        quantity: Decimal,
        revenue: Decimal,
        cogs: Decimal,
        profit: Decimal,
        sign: int,
    ) -> None:
        self.quantity += sign * quantity
        self.revenue += sign * revenue
        self.cogs += sign * cogs
        self.profit += sign * profit


class _OperationDay:
    def __init__(self) -> None:
        self.new_members = 0
        self.expired_memberships = 0
        self.check_ins = 0
        self.unique_attendees = 0


def _product_days(*, gym_id: UUID, date_from: date, date_to: date) -> dict[date, _ProductMetric]:
    days: dict[date, _ProductMetric] = {}
    _add_product_days(
        days,
        _dated_sale_rows(gym_id=gym_id, date_from=date_from, date_to=date_to, correction=False),
        sign=1,
    )
    _add_product_days(
        days,
        _dated_sale_rows(gym_id=gym_id, date_from=date_from, date_to=date_to, correction=True),
        sign=-1,
    )
    _add_product_days(
        days,
        _dated_return_rows(gym_id=gym_id, date_from=date_from, date_to=date_to),
        sign=-1,
    )
    for metric in days.values():
        metric.quantity = metric.quantity.quantize(_ZERO_QTY)
        metric.revenue = _money(metric.revenue)
        metric.cogs = _money(metric.cogs)
        metric.profit = _money(metric.profit)
    return days


def _add_product_days(
    days: dict[date, _ProductMetric],
    rows: list[dict[str, object]],
    sign: int,
) -> None:
    for row in rows:
        business_date = row.get("business_date")
        if isinstance(business_date, datetime):
            business_date = business_date.date()
        if not isinstance(business_date, date):
            continue
        days.setdefault(business_date, _ProductMetric()).add(
            quantity=_quantity(row.get("quantity")),
            revenue=_money(row.get("revenue")),
            cogs=_money(row.get("cogs")),
            profit=_money(row.get("profit")),
            sign=sign,
        )


def _dated_sale_rows(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
    correction: bool,
) -> list[dict[str, object]]:
    queryset = RetailSaleLine.objects.filter(gym_id=gym_id, sale__gym_id=gym_id)
    date_field = "sale__void_business_date" if correction else "sale__business_date"
    if correction:
        queryset = queryset.filter(
            sale__status=RetailSale.Status.VOID,
            sale__void_business_date__gte=date_from,
            sale__void_business_date__lte=date_to,
        )
    else:
        queryset = queryset.filter(
            sale__status__in=[RetailSale.Status.POSTED, RetailSale.Status.VOID],
            sale__business_date__gte=date_from,
            sale__business_date__lte=date_to,
        )
    return [
        {
            "business_date": row[date_field],
            "quantity": row["quantity"],
            "revenue": row["revenue"],
            "cogs": row["cogs"],
            "profit": row["profit"],
        }
        for row in queryset.order_by()
        .values(date_field)
        .annotate(
            quantity=Coalesce(Sum("quantity"), _ZERO_QTY),
            revenue=Coalesce(Sum("line_revenue"), _ZERO),
            cogs=Coalesce(Sum("cost_of_goods_sold"), _ZERO),
            profit=Coalesce(Sum("gross_profit"), _ZERO),
        )
    ]


def _dated_return_rows(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
) -> list[dict[str, object]]:
    queryset = RetailReturnLine.objects.filter(
        gym_id=gym_id,
        retail_return__gym_id=gym_id,
        sale_line__gym_id=gym_id,
        retail_return__business_date__gte=date_from,
        retail_return__business_date__lte=date_to,
    )
    return [
        {
            "business_date": row["retail_return__business_date"],
            "quantity": row["quantity"],
            "revenue": row["revenue"],
            "cogs": row["cogs"],
            "profit": row["profit"],
        }
        for row in queryset.order_by()
        .values("retail_return__business_date")
        .annotate(
            quantity=Coalesce(Sum("quantity"), _ZERO_QTY),
            revenue=Coalesce(Sum("refund_revenue"), _ZERO),
            cogs=Coalesce(Sum("cost_of_goods_sold"), _ZERO),
            profit=Coalesce(Sum("gross_profit"), _ZERO),
        )
    ]


def _operation_days(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> dict[date, _OperationDay]:
    days: dict[date, _OperationDay] = {}
    members = (
        Member.objects.filter(
            gym_id=gym_id,
            join_date__gte=date_from,
            join_date__lte=date_to,
        )
        .order_by()
        .values("join_date")
        .annotate(total=Count("id"))
    )
    for row in members:
        joined = _row_date(row, "join_date")
        if joined is not None:
            days.setdefault(joined, _OperationDay()).new_members = _count(row["total"])
    expired = (
        Membership.objects.filter(
            gym_id=gym_id,
            expiry_date__gte=date_from,
            expiry_date__lte=date_to,
        )
        .order_by()
        .values("expiry_date")
        .annotate(total=Count("id"))
    )
    for row in expired:
        expiry = _row_date(row, "expiry_date")
        if expiry is not None:
            days.setdefault(expiry, _OperationDay()).expired_memberships = _count(row["total"])
    zone = ZoneInfo(timezone_name)
    start, end = _local_window(date_from, date_to, zone)
    visits = (
        CheckIn.objects.filter(gym_id=gym_id, check_in_at__gte=start, check_in_at__lt=end)
        .annotate(local_date=TruncDate("check_in_at", tzinfo=zone))
        .order_by()
        .values("local_date")
        .annotate(visits=Count("id"), people=Count("member_id", distinct=True))
    )
    for row in visits:
        local_date = _row_date(row, "local_date")
        if local_date is None:
            continue
        day = days.setdefault(local_date, _OperationDay())
        day.check_ins = _count(row["visits"])
        day.unique_attendees = _count(row["people"])
    return days


def _range_unique_attendees(
    *,
    gym_id: UUID,
    timezone_name: str,
    date_from: date,
    date_to: date,
) -> int:
    start, end = _local_window(date_from, date_to, ZoneInfo(timezone_name))
    stats = CheckIn.objects.filter(
        gym_id=gym_id,
        check_in_at__gte=start,
        check_in_at__lt=end,
    ).aggregate(attendees=Count("member_id", distinct=True))
    return _count(stats["attendees"])


def _local_window(date_from: date, date_to: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _row_date(row: dict[str, object], key: str) -> date | None:
    value = row.get(key)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _parse_money(value: object) -> Decimal:
    if not isinstance(value, str):
        return _ZERO
    return _money(Decimal(value))


def _quantity_text(value: object) -> Decimal:
    if not isinstance(value, str):
        return _ZERO_QTY
    return _quantity(Decimal(value))


def _sum(condition: Q) -> Coalesce:
    return Coalesce(Sum("amount", filter=condition), _ZERO)


def _money(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        return _ZERO
    quantized = value.quantize(_ZERO)
    if quantized == 0:
        return _ZERO
    return quantized


def _quantity(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        return _ZERO_QTY
    return value.quantize(_ZERO_QTY)

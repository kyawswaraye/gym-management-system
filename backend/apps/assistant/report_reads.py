"""Dashboard totals through the existing report selector.

Financial amounts stay out unless this membership has reports.financial.
The tool does not invent a range the staff did not ask for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date

from django.utils import timezone
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.services import gym_today
from apps.reports.inventory_reports import (
    inventory_report,
    product_sales_report,
    stock_movement_report,
)
from apps.reports.ranges import (
    MAX_REPORT_ROWS,
    PRESETS,
    inclusive_days,
    month_bounds,
    resolve_preset,
)
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
from apps.tenants.models import GymSettings
from apps.trainers.selectors import deny_trainer_only, require_own_active_trainer_profile_id
from apps.users.serializers import StrictSerializer

GET_GYM_DASHBOARD = "get_gym_dashboard"
GET_GYM_REPORT = "get_gym_report"
GET_TRAINER_DASHBOARD = "get_trainer_dashboard"
REPORT_READ_TOOL_NAMES = (GET_GYM_DASHBOARD, GET_GYM_REPORT, GET_TRAINER_DASHBOARD)
_FINANCIAL_REPORTS = frozenset({"period", "cash_book", "profit_loss", "product_sales"})
_OPERATIONAL_REPORTS = frozenset(
    {
        "membership_expiry",
        "check_ins",
        "pt_sessions",
        "classes",
        "stock_movements",
        "inventory",
    }
)
_REPORTS = _FINANCIAL_REPORTS | _OPERATIONAL_REPORTS
_REPORT_LIMIT = 10000


class DashboardReadQuerySerializer(StrictSerializer):
    range = serializers.CharField(required=False, allow_blank=True, max_length=32)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if (start is None) != (end is None):
            raise serializers.ValidationError({"date_to": ["Provide both dates or a named range."]})
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError({"date_to": ["Must be on or after date_from."]})
        return attrs


class GymReportQuerySerializer(StrictSerializer):
    report = serializers.ChoiceField(choices=sorted(_REPORTS))
    range = serializers.CharField(required=False, allow_blank=True, max_length=32)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if (start is None) != (end is None):
            raise serializers.ValidationError({"date_to": ["Provide both dates or a named range."]})
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError({"date_to": ["Must be on or after date_from."]})
        return attrs


class TrainerReportQuerySerializer(StrictSerializer):
    view = serializers.ChoiceField(choices=("summary", "daily"), required=False, default="summary")
    range = serializers.CharField(required=False, allow_blank=True, max_length=32)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if (start is None) != (end is None):
            raise serializers.ValidationError({"date_to": ["Provide both dates or a named range."]})
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError({"date_to": ["Must be on or after date_from."]})
        return attrs


def report_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def report_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def get_gym_dashboard(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    deny_trainer_only(gym_id=call.gym.id, actor=call.actor)
    serializer = DashboardReadQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    today = gym_today(call.gym)
    start, end = _bounds(call, query, today)
    include_financial = "reports.financial" in call.permission_codes
    payload = tenant_dashboard(
        gym=call.gym,
        date_from=start,
        date_to=end,
        today=today,
        include_financial=include_financial,
    )
    operational = payload["operational"]
    financial = payload["financial"]
    result: dict[str, object] = {
        "range": payload["range"],
        "timezone": payload["timezone"],
        "currency": payload["currency"],
        "operational": {
            "active_members": operational["active_members"],
            "new_members": operational["new_members"],
            "check_ins": operational["check_ins"],
            "expired_memberships": operational["expired_memberships"],
            "pending_approvals": operational["pending_approvals"],
        },
        "financial": None,
    }
    if financial is not None:
        result["financial"] = _financial_view(financial)
    return result


def get_gym_report(call: ReadCall) -> dict[str, object]:
    """Return one official report. Money stays out without reports.financial."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    deny_trainer_only(gym_id=call.gym.id, actor=call.actor)
    serializer = GymReportQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    kind = str(query["report"])
    if kind in _FINANCIAL_REPORTS and "reports.financial" not in call.permission_codes:
        return {"report": kind, "financial": None, "omitted": "reports.financial"}
    generated = timezone.now()
    if kind == "inventory":
        payload = inventory_report(
            gym=call.gym,
            actor_membership_id=call.actor.id,
            limit=int(query.get("limit") or 25),
            after=str(query.get("after") or ""),
            generated_at=generated,
        )
        return _public_report(kind, payload)
    today = gym_today(call.gym)
    start, end = _report_bounds(call, query, today)
    if kind == "period":
        payload = period_report(gym=call.gym, date_from=start, date_to=end, generated_at=generated)
    elif kind == "cash_book":
        payload = cash_book_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    elif kind == "profit_loss":
        payload = profit_loss_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    elif kind == "product_sales":
        payload = product_sales_report(
            gym=call.gym,
            actor_membership_id=call.actor.id,
            date_from=start,
            date_to=end,
            limit=int(query.get("limit") or 25),
            after=str(query.get("after") or ""),
            generated_at=generated,
        )
    elif kind == "membership_expiry":
        payload = membership_expiry_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    elif kind == "check_ins":
        payload = check_in_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    elif kind == "pt_sessions":
        payload = pt_session_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    elif kind == "classes":
        payload = class_report(gym=call.gym, date_from=start, date_to=end, generated_at=generated)
    elif kind == "stock_movements":
        payload = stock_movement_report(
            gym=call.gym, date_from=start, date_to=end, generated_at=generated
        )
    else:
        raise ExecutionRejected("This report is not registered.", code="unknown_tool")
    return _public_report(kind, payload)


def get_trainer_dashboard(call: ReadCall) -> dict[str, object]:
    """Return this signed-in trainer's own sessions. Another trainer cannot be named."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    serializer = TrainerReportQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    profile_id = require_own_active_trainer_profile_id(gym_id=call.gym.id, actor=call.actor)
    today = gym_today(call.gym)
    start, end = _report_bounds(call, query, today)
    generated = timezone.now()
    if str(query.get("view") or "summary") == "daily":
        payload = trainer_session_report(
            gym=call.gym,
            trainer_staff_profile_id=profile_id,
            date_from=start,
            date_to=end,
            generated_at=generated,
        )
    else:
        payload = trainer_dashboard(
            gym=call.gym,
            trainer_staff_profile_id=profile_id,
            date_from=start,
            date_to=end,
            today=today,
            generated_at=generated,
        )
    return _public_report("trainer", payload)


def _report_bounds(call: ReadCall, query: Mapping[str, object], today: date) -> tuple[date, date]:
    preset = str(query.get("range") or "")
    if preset and preset not in PRESETS and preset != "next_month":
        raise ExecutionRejected("The report range is not recognized.", code="invalid_range")
    start, end = _bounds(call, query, today)
    if inclusive_days(start, end) > MAX_REPORT_ROWS:
        raise ExecutionRejected(
            "A daily report covers at most 31 days. Use get_gym_dashboard for a longer total.",
            code="range_too_long",
        )
    return start, end


def _public_report(kind: str, payload: Mapping[str, object]) -> dict[str, object]:
    parsed = json.loads(json.dumps(payload, default=str))
    if not isinstance(parsed, dict):
        return {"report": kind}
    parsed.pop("gym", None)
    parsed.pop("source_status_policy", None)
    parsed["report"] = kind
    if len(json.dumps(parsed)) > _REPORT_LIMIT and "rows" in parsed:
        parsed.pop("rows", None)
        parsed["rows_omitted"] = True
    return parsed


def _bounds(call: ReadCall, query: Mapping[str, object], today: date) -> tuple[date, date]:
    preset = str(query.get("range") or "")
    if preset == "next_month":
        if today.month == 12:
            return month_bounds(today.year + 1, 1)
        return month_bounds(today.year, today.month + 1)
    if preset in PRESETS:
        settings = GymSettings.objects.filter(gym_id=call.gym.id).only("week_starts_on").first()
        week_starts_on = 1 if settings is None else int(settings.week_starts_on)
        return resolve_preset(preset, today=today, week_starts_on=week_starts_on)
    start = query.get("date_from")
    end = query.get("date_to")
    if isinstance(start, date) and isinstance(end, date):
        return start, end
    return today, today


_FINANCIAL_TEXT = (
    "new_member_revenue",
    "renewal_revenue",
    "pt_revenue",
    "class_revenue",
    "product_revenue",
    "other_revenue",
    "total_revenue",
    "total_expenses",
    "staff_salary_expense",
    "pt_commission_expense",
    "net_profit",
    "pending_payment_amount",
)


def _financial_view(financial: Mapping[str, object]) -> dict[str, object]:
    """Expose the dashboard's stored categories. Total revenue stays their sum."""

    viewed: dict[str, object] = {key: financial[key] for key in _FINANCIAL_TEXT if key in financial}
    pending_members = financial.get("pending_payment_member_count")
    if isinstance(pending_members, int):
        viewed["pending_payment_member_count"] = pending_members
    ranked = financial.get("revenue_sources_ranked")
    if isinstance(ranked, list):
        viewed["revenue_sources_ranked"] = ranked
    categories = financial.get("expenses_by_category")
    if isinstance(categories, list):
        viewed["expenses_by_category"] = categories
    products = financial.get("top_product_items")
    if isinstance(products, list):
        viewed["top_product_items"] = [_public_product(item) for item in products]
    return viewed


def _public_product(item: object) -> object:
    if not isinstance(item, Mapping):
        return item
    copied = dict(item)
    product_id = copied.get("product_id")
    if product_id is not None:
        copied["product_id"] = str(product_id)
    return copied


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


def _definition() -> ToolDefinition:
    return ToolDefinition(
        name=GET_GYM_DASHBOARD,
        description=(
            "Read gym dashboard totals for a gym-local range: active members, "
            "new members, check-ins, expired memberships, and separate stored "
            "money fields. new_member_revenue, renewal_revenue, pt_revenue, "
            "class_revenue, product_revenue, and other_revenue are not "
            "interchangeable. total_revenue is only their sum. Membership "
            "revenue is new_member_revenue plus renewal_revenue, not "
            "total_revenue. Also returns total_expenses, net_profit, "
            "expenses_by_category, revenue_sources_ranked, and top_product_items. "
            "net_profit is total_revenue minus total_expenses from this result. "
            "Pass range "
            "when the staff names a period: today, this_week, this_month, "
            "last_month, this_year, or next_month. Or pass both date_from "
            "and date_to. If range and dates are omitted, the result is today "
            "only. For two periods, call this tool once per period. Use "
            "get_gym_report when the question needs the official daily rows, "
            "not only this total. Financial amounts are omitted without "
            "reports.financial."
        ),
        module="reports",
        operation="read",
        permissions=("reports.view",),
        features=("reports",),
    )


_READS = {
    GET_GYM_DASHBOARD: ToolContract(
        definition=_definition(),
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=GET_GYM_DASHBOARD,
            description="Dashboard totals for one gym-local range.",
            fields=(
                FieldRequirement(
                    "range",
                    "today, this_week, this_month, last_month, this_year, or next_month. Optional.",
                    required=False,
                ),
                FieldRequirement("date_from", "First gym-local date. Optional.", required=False),
                FieldRequirement("date_to", "Last gym-local date. Optional.", required=False),
            ),
        ),
    ),
    GET_GYM_REPORT: ToolContract(
        definition=ToolDefinition(
            name=GET_GYM_REPORT,
            description=(
                "Read one official gym report for at most 31 gym-local days. "
                "report is required: period (daily revenue, expenses, and "
                "attendance rows), cash_book, profit_loss, product_sales, "
                "membership_expiry, check_ins, pt_sessions, classes, "
                "stock_movements, or inventory. inventory is the current "
                "catalog and does not use a date range. profit_loss net profit "
                "is total revenue minus total expenses. Product cost sits beside "
                "that profit and is not subtracted again. Pass range today, "
                "this_week, this_month, last_month, or next_month, or both "
                "date_from and date_to. A longer range returns range_too_long; "
                "use get_gym_dashboard for that total. Financial reports return "
                "omitted reports.financial and no amounts when this staff member "
                "cannot see money. This is the whole gym, not one trainer."
            ),
            module="reports",
            operation="read",
            permissions=("reports.view",),
            features=("reports",),
        ),
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=GET_GYM_REPORT,
            description="One official gym report.",
            fields=(
                FieldRequirement(
                    "report",
                    "period, cash_book, profit_loss, product_sales, membership_expiry, "
                    "check_ins, pt_sessions, classes, stock_movements, or inventory.",
                    required=True,
                ),
                FieldRequirement(
                    "range",
                    "today, this_week, this_month, last_month, or next_month. Optional.",
                    required=False,
                ),
                FieldRequirement("date_from", "First gym-local date. Optional.", required=False),
                FieldRequirement("date_to", "Last gym-local date. Optional.", required=False),
                FieldRequirement("limit", "Page size, 1 to 25. Optional.", required=False),
                FieldRequirement(
                    "after",
                    "Cursor from the previous page. Optional.",
                    required=False,
                ),
            ),
        ),
    ),
    GET_TRAINER_DASHBOARD: ToolContract(
        definition=ToolDefinition(
            name=GET_TRAINER_DASHBOARD,
            description=(
                "Read the signed-in trainer's own personal-training book: active "
                "assigned members, session counts, and upcoming bookings. view "
                "summary is the default. view daily returns one row per day for "
                "at most 31 days. Do not send a trainer id or another person's "
                "name. If this returns not_found, this staff member is not that "
                "trainer; use search_trainers, search_pt_bookings, and "
                "get_gym_dashboard. This result has no money."
            ),
            module="reports",
            operation="read",
            permissions=("reports.view",),
            features=("reports", "pt"),
        ),
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=GET_TRAINER_DASHBOARD,
            description="The signed-in trainer's own sessions.",
            fields=(
                FieldRequirement(
                    "view",
                    "summary or daily. Optional. summary is the default.",
                    required=False,
                ),
                FieldRequirement(
                    "range",
                    "today, this_week, this_month, last_month, or next_month. Optional.",
                    required=False,
                ),
                FieldRequirement("date_from", "First gym-local date. Optional.", required=False),
                FieldRequirement("date_to", "Last gym-local date. Optional.", required=False),
            ),
        ),
    ),
}
_READERS = {
    GET_GYM_DASHBOARD: get_gym_dashboard,
    GET_GYM_REPORT: get_gym_report,
    GET_TRAINER_DASHBOARD: get_trainer_dashboard,
}

"""Dashboard totals through the existing report selector.

Financial amounts stay out unless this membership has reports.financial.
The tool does not invent a range the staff did not ask for.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.services import gym_today
from apps.reports.ranges import PRESETS, month_bounds, resolve_preset
from apps.reports.selectors import tenant_dashboard
from apps.tenants.models import GymSettings
from apps.trainers.selectors import deny_trainer_only
from apps.users.serializers import StrictSerializer

GET_GYM_DASHBOARD = "get_gym_dashboard"
REPORT_READ_TOOL_NAMES = (GET_GYM_DASHBOARD,)


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
            "expenses_by_category, and revenue_sources_ranked. Pass range "
            "when the staff names a period: today, this_week, this_month, "
            "last_month, this_year, or next_month. Or pass both date_from "
            "and date_to. If range and dates are omitted, the result is today "
            "only. For two periods, call this tool once per period. Financial "
            "amounts are omitted without reports.financial."
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
    )
}
_READERS = {GET_GYM_DASHBOARD: get_gym_dashboard}

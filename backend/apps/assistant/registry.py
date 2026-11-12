from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDefinition:
    """One staff operation the assistant may call.

    External side effects are named so callers can see them. The definition
    cannot mark those effects as covered by the database claim.
    """

    name: str
    description: str
    module: str
    operation: str
    permissions: tuple[str, ...]
    features: tuple[str, ...] = ()
    requires_recent_mfa: bool = False
    requires_active_owner: bool = False
    confirmation_required: bool = False
    approval_required: bool = False
    external_side_effects: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.operation not in {"read", "write"}:
            raise ValueError("Tool operation must be read or write.")
        if not tool.name or tool.name in self._tools:
            raise ValueError("Tool name must be unique.")
        if not tool.permissions and not tool.requires_active_owner:
            raise ValueError("Tool must name at least one permission.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def authorized(self, *, permission_codes: frozenset[str]) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if tool.permissions and set(tool.permissions).issubset(permission_codes)
        )


def closed_production_names() -> tuple[str, ...]:
    """Return the names registered after their security tests passed."""

    from apps.assistant.activity_tools import ACTIVITY_TOOL_NAMES
    from apps.assistant.announcement_reads import ANNOUNCEMENT_READ_TOOL_NAMES
    from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
    from apps.assistant.cash_tools import CASH_TOOL_NAMES
    from apps.assistant.class_reads import CLASS_READ_TOOL_NAMES
    from apps.assistant.commission_reads import COMMISSION_READ_TOOL_NAMES
    from apps.assistant.expense_tools import EXPENSE_TOOL_NAMES
    from apps.assistant.finance_reads import FINANCE_READ_TOOL_NAMES
    from apps.assistant.inventory_tools import INVENTORY_TOOL_NAMES
    from apps.assistant.member_reads import MEMBER_READ_TOOL_NAMES
    from apps.assistant.member_tools import MEMBER_TOOL_NAMES
    from apps.assistant.membership_reads import MEMBERSHIP_READ_TOOL_NAMES
    from apps.assistant.operations_reads import OPERATIONS_READ_TOOL_NAMES
    from apps.assistant.package_reads import PACKAGE_READ_TOOL_NAMES
    from apps.assistant.payment_reads import PAYMENT_READ_TOOL_NAMES
    from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES
    from apps.assistant.pt_tools import PT_TOOL_NAMES
    from apps.assistant.report_reads import REPORT_READ_TOOL_NAMES
    from apps.assistant.retail_tools import RETAIL_TOOL_NAMES
    from apps.assistant.salary_tools import SALARY_TOOL_NAMES
    from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
    from apps.assistant.staff_tools import STAFF_TOOL_NAMES
    from apps.assistant.training_tools import TRAINING_TOOL_NAMES

    return tuple(
        sorted(
            set(MEMBER_TOOL_NAMES)
            | set(MEMBER_READ_TOOL_NAMES)
            | set(MEMBERSHIP_READ_TOOL_NAMES)
            | set(PACKAGE_READ_TOOL_NAMES)
            | set(CLASS_READ_TOOL_NAMES)
            | set(COMMISSION_READ_TOOL_NAMES)
            | set(FINANCE_READ_TOOL_NAMES)
            | set(OPERATIONS_READ_TOOL_NAMES)
            | set(STAFF_TOOL_NAMES)
            | set(ACTIVITY_TOOL_NAMES)
            | set(ANNOUNCEMENT_READ_TOOL_NAMES)
            | set(REPORT_READ_TOOL_NAMES)
            | set(TRAINING_TOOL_NAMES)
            | set(SCHEDULE_TOOL_NAMES)
            | set(BOOKING_TOOL_NAMES)
            | set(INVENTORY_TOOL_NAMES)
            | set(CASH_TOOL_NAMES)
            | set(PAYMENT_TOOL_NAMES)
            | set(PAYMENT_READ_TOOL_NAMES)
            | set(PT_TOOL_NAMES)
            | set(EXPENSE_TOOL_NAMES)
            | set(RETAIL_TOOL_NAMES)
            | set(SALARY_TOOL_NAMES)
        )
    )


def production_registry() -> ToolRegistry:
    """Return the tools whose security tests have passed.

    Member, membership, staff, role, gym, announcement, check-in, trainer,
    package, class-type, class-schedule, occurrence-cancel, class-booking, stock,
    cash-session, payment, receipt, expense, retail-sale, salary-period, PT sale,
    commission, package, and PT booking writes are registered. Payment, expense,
    retail, salary, and PT sale approval and rejection are registered. Payment
    search, payment detail, member search, member detail, membership search,
    membership plan search, granted package search, class occurrence search,
    class booking search,
    retail sale search, expense search, salary period search, cash session
    search, and PT commission search reads are registered. Mail, files, and push
    delivery stay out.
    """

    from apps.assistant.activity_tools import activity_tool_definitions
    from apps.assistant.announcement_reads import announcement_read_tool_definitions
    from apps.assistant.booking_tools import booking_tool_definitions
    from apps.assistant.cash_tools import cash_tool_definitions
    from apps.assistant.class_reads import class_read_tool_definitions
    from apps.assistant.commission_reads import commission_read_tool_definitions
    from apps.assistant.expense_tools import expense_tool_definitions
    from apps.assistant.finance_reads import finance_read_tool_definitions
    from apps.assistant.inventory_tools import inventory_tool_definitions
    from apps.assistant.member_reads import member_read_tool_definitions
    from apps.assistant.member_tools import member_tool_definitions
    from apps.assistant.membership_reads import membership_read_tool_definitions
    from apps.assistant.operations_reads import operations_read_tool_definitions
    from apps.assistant.package_reads import package_read_tool_definitions
    from apps.assistant.payment_reads import payment_read_tool_definitions
    from apps.assistant.payment_tools import payment_tool_definitions
    from apps.assistant.pt_tools import pt_tool_definitions
    from apps.assistant.report_reads import report_read_tool_definitions
    from apps.assistant.retail_tools import retail_tool_definitions
    from apps.assistant.salary_tools import salary_tool_definitions
    from apps.assistant.schedule_tools import schedule_tool_definitions
    from apps.assistant.staff_tools import staff_tool_definitions
    from apps.assistant.training_tools import training_tool_definitions

    registry = ToolRegistry()
    for tool in (
        *member_tool_definitions(),
        *member_read_tool_definitions(),
        *membership_read_tool_definitions(),
        *package_read_tool_definitions(),
        *staff_tool_definitions(),
        *activity_tool_definitions(),
        *announcement_read_tool_definitions(),
        *report_read_tool_definitions(),
        *training_tool_definitions(),
        *schedule_tool_definitions(),
        *booking_tool_definitions(),
        *inventory_tool_definitions(),
        *cash_tool_definitions(),
        *class_read_tool_definitions(),
        *commission_read_tool_definitions(),
        *payment_tool_definitions(),
        *payment_read_tool_definitions(),
        *pt_tool_definitions(),
        *expense_tool_definitions(),
        *finance_read_tool_definitions(),
        *operations_read_tool_definitions(),
        *retail_tool_definitions(),
        *salary_tool_definitions(),
    ):
        registry.register(tool)
    return registry

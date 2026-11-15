"""Ordered staff workflows. A turn loads only the tools and steps it needs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

MODULE_SECTIONS: dict[str, tuple[str, ...]] = {
    "members": ("members",),
    "memberships": ("memberships",),
    "checkins": ("checkins",),
    "staff": ("staff",),
    "roles": ("staff",),
    "gym": ("settings",),
    "owners": ("staff",),
    "classes": ("classes",),
    "trainers": ("packages", "assignments", "pt_sales", "staff", "cash"),
    "pt": ("packages", "assignments", "pt_sales"),
    "payments": ("payments", "memberships"),
    "cashbook": ("cash",),
    "finance": ("retail", "expenses"),
    "expenses": ("expenses",),
    "retail": ("retail", "inventory"),
    "inventory": ("inventory",),
    "salaries": ("salaries", "staff"),
    "announcements": ("announcements",),
    "reports": (
        "members",
        "memberships",
        "checkins",
        "payments",
        "expenses",
        "salaries",
        "retail",
        "cash",
        "classes",
        "packages",
    ),
}


@dataclass(frozen=True)
class Workflow:
    """One staff task: a short catalog line, the ordered steps, and its modules."""

    id: str
    summary: str
    guide: str
    modules: tuple[str, ...]
    all_reads: bool = False


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        id="cash_session_open",
        summary="Open the till before any cash payment, sale, or expense can be approved.",
        guide=(
            "1. Ask the register code and the opening cash when either is missing. "
            "Do not invent either one. 2. Call open_cash_session. The change is only "
            "proposed until the staff confirms it. 3. A CASH payment, retail sale, "
            "or expense cannot be approved until a cash session is OPEN. "
            "4. Use search_cash_sessions to see the open till. A receptionist can "
            "use only the session they opened. Do not invent a till id, and do not "
            "send one in the request. Counting and closing the drawer is a separate "
            "workflow."
        ),
        modules=("cashbook",),
    ),
    Workflow(
        id="member_registration",
        summary="Register a person. The server assigns the member number.",
        guide=(
            "1. Ask for the name and join date when either is missing. Myanmar names "
            "usually have no family name; put the whole name in first_name and leave "
            "last_name empty; do not ask for a last name and do not split a name like "
            '"Hla Hla" into two parts. A name alone is not a registration. Do not call '
            "create_member until the staff states a join date or today. Do not fill "
            "today yourself. Phone and notes are optional. 2. Call create_member. The "
            "server assigns the member number. Do not invent one. 3. search_members "
            "lists matches. get_member opens one member. If the result is ambiguous, "
            "list the choices and ask which one. Do not choose. 4. update_member "
            "changes name, phone, email, or notes. archive_member and restore_member "
            "change whether the person is kept. Registration does not sell a membership "
            "or record a payment."
        ),
        modules=("members",),
    ),
    Workflow(
        id="membership_sale",
        summary="Sell a plan as a new membership. A renewal is a new membership.",
        guide=(
            "1. A membership needs a member, an active plan, and a start date. Use "
            "search_members and search_membership_plans when those are not verified. "
            "Ask for the missing one. 2. Call create_membership. Pending, active, and "
            "suspended ranges cannot overlap. This does not record a payment. "
            "3. A renewal is a new membership. There is no tool that extends dates. "
            "4. suspend_membership freezes a term. resume_membership continues a "
            "suspended membership. cancel_membership needs a reason. 5. create_plan, "
            "update_plan, and archive_plan change the catalog. Ask for code, name, "
            "duration, and price before creating a plan. Use search_membership_expiries "
            "for members nearing the end date."
        ),
        modules=("memberships", "members"),
    ),
    Workflow(
        id="membership_payment",
        summary="Draft, submit, and approve a membership payment, then receipt, void, or refund.",
        guide=(
            "1. Ask for the member, membership, amount, method, and business date "
            "when any is missing. Use search_payments or get_payment to read a row. "
            "2. create_payment stores a DRAFT. A draft is not posted, does not mark "
            "the membership paid, and does not issue a receipt. 3. submit_payment, "
            "then approve_payment. The creator cannot approve their own record. Send "
            "the payment's own id. The server supplies the amount. If it is not "
            "waiting for approval, submit that record first. 4. A CASH approval needs an open "
            "cash session. Do not choose the till. 5. reject_payment needs a reason "
            "and posts nothing. cancel_payment is only for a draft. 6. void_payment "
            "and refund_payment apply to a posted charge. A refund is a payment whose "
            "entry type is REFUND. void_receipt voids the paper only. update_payment "
            "changes a draft."
        ),
        modules=("payments", "members", "memberships", "cashbook"),
    ),
    Workflow(
        id="check_in",
        summary="Check a member in or out. The visit needs an active paid membership.",
        guide=(
            "1. Check-in needs an active member and an active paid membership that "
            "has not expired. Use search_members and search_memberships when that "
            "is not verified. Ask which member when more than one matches. "
            "2. create_manual_check_in records the visit. A duplicate visit or an "
            "ineligible member is refused. Say the reason the tool returns. "
            "3. check_out_check_in closes today's open visit. Checking out does not "
            "end the membership. 4. Use search_check_ins for history."
        ),
        modules=("checkins", "members", "memberships"),
    ),
    Workflow(
        id="personal_training",
        summary="Assign a trainer, sell PT, grant sessions, book, attend, and pay commission.",
        guide=(
            "1. search_service_packages is the catalog. Ask package, trainer, "
            "member, and date when missing. search_trainers lists profiles. "
            "search_trainer_assignments with summary true counts members per "
            "trainer even if that list was empty. "
            "2. create_assignment starts on an effective date. A sale or booking "
            "needs that assignment. end_assignment and cancel_assignment close one. "
            "3. create_pt_sale is a DRAFT. submit_pt_sale, then approve_pt_sale. "
            "CASH needs an OPEN session from cash_session_open. reject_pt_sale "
            "needs a reason. cancel_pt_sale is only a draft. void_pt_sale reverses "
            "posted payouts. 4. grant_member_package grants sessions. "
            "search_pt_packages shows sessions left. 5. create_pt_booking needs "
            "the assignment and sessions left when metered. attend_pt_booking "
            "and mark_pt_booking_no_show consume a session when settings say so. "
            "6. record_pt_commission_payment cannot exceed commission still owed."
        ),
        modules=("trainers", "pt", "members"),
    ),
    Workflow(
        id="class_lifecycle",
        summary="Define a class, schedule it, cancel one meeting, book, and mark attendance.",
        guide=(
            "1. create_class_type stores name, duration, and capacity. "
            "update_class_type changes only that type. search_class_types reads them. "
            "2. create_class_schedule repeats it on weekdays, 0 Monday through 6 "
            "Sunday, for one trainer and date range. A one-day schedule meets on "
            "that weekday. update_class_schedule changes it. search_class_schedules "
            "lists them. 3. cancel_class_occurrence cancels one meeting. "
            "search_class_occurrences finds it. 4. create_class_booking puts one "
            "member on one occurrence with room. A duplicate is refused. A metered "
            "booking needs a class package with sessions left; grant it with "
            "grant_member_package under personal_training. cancel_class_booking "
            "cancels the booking, not the class. 5. attend_class_booking waits "
            "until the class has started. mark_class_booking_no_show waits until "
            "it has ended. search_bookings reads them. Ask the class and time "
            "when they are missing."
        ),
        modules=("classes", "members"),
    ),
    Workflow(
        id="retail_inventory",
        summary="Products, stock, and a retail sale from draft through return.",
        guide=(
            "1. create_product_category, then create_product. Ask SKU, name, unit, "
            "sale price, and cost when missing. Send the unit in capitals, such as "
            "PIECE. update_product changes name or price. search_products shows "
            "quantity on hand. search_stock_movements lists movements. "
            "2. adjust_stock records OPENING, PURCHASE, DAMAGE, LOSS, ADJUSTMENT, "
            "or CORRECTION. After create_product is saved, an opening count the "
            "staff asked for is adjust_stock with movement OPENING and that "
            "quantity. Do not send SALE or SALE_RETURN. 3. create_retail_sale is "
            "a DRAFT of product and quantity. submit_retail_sale, then "
            "approve_retail_sale posts stock, a product payment, and a receipt. "
            "CASH needs an open till. 4. reject_retail_sale needs a reason. "
            "cancel_retail_sale is only a draft. return_retail_sale returns part "
            "of a posted sale. void_retail_sale voids a posted sale with no return. "
            "search_sales reads one."
        ),
        modules=("inventory", "retail", "finance"),
    ),
    Workflow(
        id="expenses",
        summary="Record an expense draft, submit it, and approve, reject, or void it.",
        guide=(
            "1. Ask for the category, amount, method, business date, and "
            "description when any is missing. search_expenses lists the rows. "
            "create_expense_category adds a category. Do not choose the protected "
            "salary or commission categories. update_expense_category renames one. "
            "2. create_expense is a DRAFT. submit_expense, then approve_expense. "
            "The creator cannot approve their own expense. A CASH approval needs "
            "an open cash session and writes one cash out. 3. reject_expense needs "
            "a reason and posts nothing. cancel_expense is only a draft. "
            "void_expense voids a posted manual expense, not a salary or commission "
            "row. update_expense changes a draft only."
        ),
        modules=("expenses", "cashbook", "finance"),
    ),
    Workflow(
        id="salaries",
        summary="Build a salary period, replace its lines, then submit and approve.",
        guide=(
            "1. Wages are typed for the period. They are not copied from the staff "
            "profile. Ask the period dates, business date, and each person's amount, "
            "currency, method, and description. Use search_staff when the person is "
            "not verified. 2. create_salary_period, then replace_salary_lines. "
            "3. submit_salary_period, then approve_salary_period. The creator cannot "
            "approve their own period. A submitted period cannot be edited. A CASH "
            "line needs an open cash session. Approval posts one salary expense per "
            "line. 4. reject_salary_period needs a reason and posts nothing. "
            "cancel_salary_period is only a draft. void_salary_period voids a posted "
            "period. search_salary_periods reads one. update_salary_period changes "
            "a draft."
        ),
        modules=("salaries", "staff", "cashbook"),
    ),
    Workflow(
        id="cash_session_close",
        summary="Count the drawer and reconcile the open cash session.",
        guide=(
            "1. Use search_cash_sessions for the open till this person may close. "
            "2. Ask the counted cash. reconcile_cash_session closes the till. The "
            "server computes the expected closing. When the count is not that "
            "expected closing, a difference reason is required. Send the expected "
            "amount when the drawer matches. 3. adjust_cash_session stores a reason "
            "before close. Its description may be blank. A closed session cannot "
            "be reopened or edited. Do not invent the expected cash."
        ),
        modules=("cashbook",),
    ),
    Workflow(
        id="announcements",
        summary="Draft an announcement and publish it to a member audience.",
        guide=(
            "1. Ask for the type (EVENT, PROMOTION, or GENERAL), title, body, and "
            "audience when any is missing. The audience is all active members, PT "
            "clients, or members expiring in 7 or 30 days. 2. create_announcement "
            "stores a DRAFT. update_announcement changes a draft. "
            "3. publish_announcement queues that announcement's in-app notices and "
            "push. Publishing does not mean every inbox was already delivered. "
            "4. cancel_announcement cancels it. search_announcements reads the "
            "status: DRAFT, SCHEDULED, PUBLISHED, or CANCELLED. Do not send a "
            "separate one-off email or file."
        ),
        modules=("announcements",),
    ),
    Workflow(
        id="staff_admin",
        summary="Staff profiles, roles, gym profile and settings, and owners.",
        guide=(
            "1. search_staff lists profiles and role names. search_staff_access "
            "reads one person's permission codes. Owners are not staff profiles. "
            "search_roles and search_permissions list the catalog. "
            "2. update_staff_profile changes the employment record. suspend_staff "
            "blocks a profile and needs a reason. activate_staff returns a "
            "suspended profile and needs a reason. A trainer profile keeps the "
            "TRAINER role when other roles change. 3. create_role, update_role, "
            "archive_role, replace_role_permissions, and replace_membership_roles "
            "change access. 4. update_gym_profile and update_gym_settings change "
            "this gym. Settings include the member-number prefix, check-in window, "
            "class cancellation cutoff, no-show sessions, finance approval, and "
            "receipt options. 5. revoke_owner and cancel_owner_invitation both "
            "need a recent multi-factor check. There is no daily shift roster."
        ),
        modules=("staff", "roles", "gym", "owners"),
    ),
    Workflow(
        id="reports",
        summary=(
            "Look up or answer a question about stored records: members, plans, "
            "packages, trainers, stock, payments, expiries, dashboards, and reports."
        ),
        guide=(
            "1. For a lookup or question, call the search for that record: "
            "search_members, search_membership_plans, search_membership_expiries, "
            "search_service_packages, search_trainers, search_products, or "
            "search_payments. Do not answer a list from the dashboard. "
            "2. get_gym_dashboard is this gym's totals for one period. Quote "
            "total_revenue and net_profit exactly from the tool result. Never "
            "add, subtract, or recompute money fields yourself. Compare two "
            "periods by calling it once each. Financial amounts are omitted "
            "without reports permission. 3. get_gym_report returns official "
            "daily rows for at most 31 days. On profit and loss, product cost "
            "sits beside the profit and is not subtracted again. "
            "4. get_trainer_dashboard is only the signed-in trainer's own "
            "sessions. Do not invent a cause for an attendance difference. "
            "State only the differences the result shows."
        ),
        modules=("reports",),
        all_reads=True,
    ),
)

_BY_ID = {item.id: item for item in WORKFLOWS}


def workflow_ids() -> tuple[str, ...]:
    """Return the catalog ids in workflow order."""

    return tuple(item.id for item in WORKFLOWS)


def get_workflow(workflow_id: str) -> Workflow | None:
    """Return one workflow, or none when the id is not in the catalog."""

    return _BY_ID.get(workflow_id)


def catalog_text() -> str:
    """One line per workflow, so the model can switch tasks."""

    return "\n".join(f"{item.id}: {item.summary}" for item in WORKFLOWS)


def guide_text(ids: Sequence[str]) -> str:
    """The ordered steps for the workflows that are active."""

    parts: list[str] = []
    for workflow_id in ids:
        item = get_workflow(workflow_id)
        if item is not None:
            parts.append(f"{item.id}\n{item.guide}")
    return "\n\n".join(parts)


def modules_for(ids: Sequence[str]) -> frozenset[str]:
    """Modules whose tools belong to the named workflows."""

    names: set[str] = set()
    for workflow_id in ids:
        item = get_workflow(workflow_id)
        if item is not None:
            names.update(item.modules)
    return frozenset(names)


def workflows_for_module(module: str) -> tuple[Workflow, ...]:
    """Workflows that include this module, in catalog order."""

    return tuple(item for item in WORKFLOWS if module in item.modules)


def sections_for(ids: Sequence[str]) -> frozenset[str]:
    """Gym-brief sections for the active workflows. Settings are always included."""

    chosen = {"settings"}
    for module in modules_for(ids):
        chosen.update(MODULE_SECTIONS.get(module, ()))
    return frozenset(chosen)

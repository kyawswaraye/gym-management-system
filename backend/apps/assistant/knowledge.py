"""What the assistant knows about GymPilot and the gym it is serving.

The domain map is the application and the database shape. The live brief is
this gym's current rows, limited to what the staff member is allowed to see.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.announcements.models import Announcement
from apps.cashbook.models import CashSession
from apps.classes.models import ClassOccurrence, ClassSchedule, ClassType
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.inventory.models import Product
from apps.members.models import CheckIn, Member, Membership, MembershipPlan, Payment
from apps.salaries.models import SalaryPeriod
from apps.sales.models import RetailSale
from apps.tenants.models import ExpenseCategory, Gym, GymSettings, StaffProfile
from apps.trainers.models import PTBooking, PTSale, ServicePackage, TrainerAssignment

logger = logging.getLogger(__name__)
_NAME_LIMIT = 30
_QTY: DecimalField[Decimal, Decimal] = DecimalField(max_digits=18, decimal_places=3)

DOMAIN_MAP = """
GymPilot is the staff application for one gym. The signed-in gym and staff
member are already chosen. Every business row belongs to that gym. Answer from
this map and from the live gym brief. Do not invent a person, amount, date, or
count that is not in the brief or in a tool result. Registered reads retrieve
a member, a membership, a membership plan and its price, a sellable PT or
class package and its price, a granted package and its remaining sessions,
a payment, a class occurrence, a class booking, a retail sale, an expense,
a salary period, a cash session, a PT commission, an announcement, and a
dashboard for active members, check-ins, and separate money fields:
new-member revenue, renewal revenue, PT revenue, class revenue, product
revenue, other revenue, total revenue, expenses, and profit. Total revenue
is the sum of those revenue fields. It is not membership revenue unless
new-member revenue plus renewal revenue equals that sum.
search_pt_trainer_sales ranks posted PT sales by trainer using stored
gross fees. search_products includes quantity_on_hand, current_unit_cost,
and inventory_value. inventory_value is those two stored fields multiplied.
There is no separate stored inventory-value total. get_gym_dashboard
net_profit is total_revenue minus total_expenses from that tool. Do not
guess a profit, a comparison, or a ranking the tool did not return.
The assistant cannot change this gym's AI subscription. That entitlement
is stored by the platform and is not a tool argument.
A refund is a payment whose entry type is REFUND. A new membership sale uses
an active plan. Use the tool result for those records. Membership expiry is
the membership end date, not the member status. Check-in history, trainer
assignments, PT sales, PT bookings, class types, class schedule definitions,
class occurrences, products, stock movements, low stock, posted product
sales, staff profiles, roles, and permissions are reads. search_service_packages
is the sellable catalog. search_pt_packages is a granted package and its
remaining sessions. search_pt_sales is the sale record. A class schedule
repeats. A class occurrence is one meeting. Low stock uses each product's
stored threshold. Changing a record is a confirmed write, and a write is not
a substitute for a read. Publishing an announcement queues its in-app notices
and push. Membership expiry email and push run when the gym settings enable
them. Passwords, sessions, audit storage, and a daily shift roster are not
supported. Say that plainly. Do not invent the record. If the requested record
is not in the brief or a tool result, it cannot be retrieved. If a brief
section is absent, this staff member cannot see it.
Say that plainly and do not reveal the hidden records. Never send the staff
to a screen.

People. A member is the customer: number, name, phone, email, gender, birth
date, join date, status ACTIVE, EXPIRED, SUSPENDED, CANCELLED, or ARCHIVED.
A membership is that member's purchased plan. The plan stores code, name,
duration DAY, WEEK, MONTH, or YEAR, and price. The membership copies that plan
and price, then keeps start date, expiry date, status PENDING, ACTIVE, EXPIRED,
SUSPENDED, or CANCELLED, payment status UNPAID, PARTIAL, PAID,
PARTIALLY_REFUNDED, or REFUNDED, and revenue source NEW_MEMBER or RENEWAL.
Pending, active, and suspended ranges cannot overlap for one member. A check-in
is one visit by MANUAL, QR, NFC, or BIOMETRIC, with check-in and optional
check-out time.

Staff. A gym membership is a person's access: INVITED, ACTIVE, SUSPENDED, or
REVOKED. The owner is a membership flag. A staff profile is the employment
record: code, display name, title, commission percent, status ACTIVE, ON_LEAVE,
TERMINATED, or ARCHIVED. There is no daily shift roster. The brief's Active
staff line is the staff list. Today's classes and Today's PT sessions name
who is scheduled today. Roles grant permissions. Custom roles can be created,
updated, archived, and assigned.

Training. A trainer assignment links one staff profile to one member from an
effective date. Status is ACTIVE, ENDED, or CANCELLED. A service package is a
sellable block of PT or CLASS sessions with a validity DAY, WEEK, MONTH, or
YEAR. A posted PT sale or an admin grant gives the member a package: ACTIVE,
EXHAUSTED, EXPIRED, or VOID. A PT booking is one session: BOOKED, ATTENDED,
NO_SHOW, or CANCELLED. Metered attendance and no-show consume one session. The
trainer must be assigned on the service date before a PT sale or PT booking.

Classes. A class type is the catalog name, duration, and capacity. A schedule
repeats it on weekdays for one trainer, location, and date range: ACTIVE,
ENDED, or CANCELLED. An occurrence is one meeting: SCHEDULED, CANCELLED, or
COMPLETED. A class booking is one member on one occurrence: BOOKED, ATTENDED,
NO_SHOW, or CANCELLED. A metered booking uses a class package.

Money. Payments, PT sales, expenses, retail sales, and salary periods share one
life: DRAFT, then PENDING_APPROVAL when separate finance approval is required,
then POSTED. A draft can be CANCELLED. A waiting document can be REJECTED. A
posted document can be VOID. A payment can also be REFUNDED. A payment is a
CHARGE, REFUND, or REVERSAL for MEMBERSHIP, PT, PRODUCT, CLASS, or OTHER, paid
by CASH, BANK_TRANSFER, CARD, MOBILE_PAYMENT, or OTHER. A posted charge can
issue a receipt. Cash payments and cash expenses post through an OPEN cash
session for that register and business date. Reconcile closes the till. Retail
sales have lines, stock cost, and optional returns. A salary period has staff
lines for a start and end date. Expenses use an expense category.

Stock. A product has a category, SKU, name, unit, sale price, and cost. On-hand
quantity is the sum of stock movements, not a column on the product. Movements
are OPENING, PURCHASE, SALE, SALE_RETURN, DAMAGE, LOSS, ADJUSTMENT, and
CORRECTION.

Gym. The gym has a name, timezone, currency, address, and subscription. Settings
hold the member-number prefix, check-in duplicate window, class cancellation
cutoff, whether a no-show consumes a session, whether finance approval is
separate, and receipt printer options. Announcements are EVENT, PROMOTION, or
GENERAL, aimed at all active members, PT clients, or members expiring in 7 or
30 days, and move DRAFT, SCHEDULED, PUBLISHED, or cancelled.

Connected records. One member can have memberships, payments, check-ins, class
bookings, a trainer assignment, PT sales, granted packages, and retail
purchases. When a question needs more than one of those, call each read. A
membership plan is gym access for a duration and price. A membership is one
member's purchased term, with its own status, dates, and payment status. A
class is a scheduled group meeting and is not a membership. A class package is
a block of class sessions. A PT package is a block of personal-training
sessions with one trainer. The platform subscription is what this gym pays for
GymPilot and is not a member membership. Suspending a membership freezes that
term. There is no command that extends a membership's dates. A renewal is a
new membership. Unpaid and partial memberships are the member debt. The
dashboard field pending_payment_amount is that outstanding membership money.
A payment purpose is MEMBERSHIP, PT, PRODUCT, CLASS, or OTHER, and the payment
belongs to the member and transaction named on the row. Dashboard revenue is
split into new_member_revenue, renewal_revenue, pt_revenue, class_revenue,
product_revenue, and other_revenue. Expenses use this gym's expense categories.
A stock purchase is a PURCHASE movement. A product sale is a retail sale that
posts a SALE movement and a PRODUCT payment. A trainer may teach classes and
deliver personal training. Compare two periods by calling get_gym_dashboard
once for each period. get_gym_report returns official daily rows for at most
31 days: period, cash_book, profit_loss, product_sales, membership_expiry,
check_ins, pt_sessions, classes, stock_movements, and inventory. On profit_loss,
net profit is total revenue minus total expenses. Product cost is reported
beside that profit and is not subtracted again. get_trainer_dashboard is only
the signed-in trainer's own sessions. A question about why attendance differs
uses the stored check-ins, class occurrences, class bookings, and PT bookings
for that day. State only the differences those results show. Do not invent a
cause.

Communications. Publishing an announcement queues that announcement's in-app
notices and push notifications through the announcement service. Membership
expiry email and membership expiry push are sent by scheduled jobs when the
gym settings enable them. The assistant does not send a separate one-off email
or file. Passwords, MFA, sessions, and audit storage are not supported. There
is no daily shift roster. If the requested record is not in the brief or a tool
result, it cannot be retrieved. A write is not saved until the server confirms it.
""".strip()


def assistant_context(gym: Gym, codes: frozenset[str], full_access: bool) -> str:
    """Return the domain map plus this gym's permitted live records."""

    try:
        facts = gym_facts(gym, codes, full_access)
    except Exception:
        logger.exception("assistant gym brief failed")
        facts = f"Live gym brief unavailable. Use the domain map and tool results. Gym: {gym.name}."
    return DOMAIN_MAP + "\n\n" + facts


def included_sections(codes: frozenset[str], full_access: bool) -> frozenset[str]:
    """Sections this staff member may see. Full access sees every section."""

    wanted = {
        "settings": ("settings.view", "gym.view"),
        "members": ("members.view",),
        "memberships": ("memberships.view",),
        "checkins": ("checkins.view",),
        "staff": ("staff.view",),
        "classes": ("classes.view",),
        "packages": ("pt.schedule.view",),
        "assignments": ("pt.assignments.view",),
        "pt_sales": ("pt.sales.view",),
        "payments": ("payments.view",),
        "expenses": ("expenses.view",),
        "salaries": ("salaries.view",),
        "retail": ("retail.view",),
        "inventory": ("inventory.view",),
        "cash": ("cashbook.view",),
        "announcements": ("announcements.view",),
    }
    if full_access:
        return frozenset(wanted)
    return frozenset(
        name for name, permissions in wanted.items() if any(code in codes for code in permissions)
    )


def gym_facts(gym: Gym, codes: frozenset[str], full_access: bool) -> str:
    """Summarize the current gym. Hidden areas are omitted, not guessed."""

    visible = included_sections(codes, full_access)
    lines = [
        (
            "Live gym brief. Partial snapshot of stored records, not a complete "
            "list and not examples. Call a read when a complete or filtered "
            "record is needed."
        ),
        (
            f"Gym: {gym.name}. Timezone {gym.timezone}. Currency {gym.currency}. "
            f"Status {gym.status}."
        ),
        f"Today is {_local_day(gym)[2].isoformat()}.",
    ]
    place = ", ".join(part for part in (gym.city, gym.region, gym.country_code) if part)
    if place:
        lines.append(f"Place: {place}.")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if "settings" in visible:
            lines.extend(_settings(gym))
        if "members" in visible:
            lines.append("Members by status: " + _status_counts(Member.objects.filter(gym=gym)))
            lines.append(
                "Recent members: "
                + _labeled(
                    Member.objects.filter(gym=gym)
                    .exclude(status=Member.Status.ARCHIVED)
                    .order_by("-join_date", "first_name")
                    .values_list("member_number", "first_name", "last_name", "status")[:_NAME_LIMIT]
                )
            )
        if "memberships" in visible:
            lines.append(
                "Memberships by status: " + _status_counts(Membership.objects.filter(gym=gym))
            )
            lines.append("Membership plans: " + _plans(gym))
        if "checkins" in visible:
            lines.append(f"Check-ins today: {_check_ins_today(gym)}.")
        if "staff" in visible:
            lines.append("Active staff: " + _staff(gym, StaffProfile.Status.ACTIVE))
            lines.append(
                "Other staff: "
                + _staff(
                    gym,
                    StaffProfile.Status.ON_LEAVE,
                    StaffProfile.Status.TERMINATED,
                )
            )
        if "classes" in visible:
            lines.append(
                "Class types: "
                + _labeled(
                    ClassType.objects.filter(gym=gym, is_active=True)
                    .order_by("name")
                    .values_list("name", "default_duration_minutes", "default_capacity")[
                        :_NAME_LIMIT
                    ]
                )
            )
            active_schedules = ClassSchedule.objects.filter(
                gym=gym, status=ClassSchedule.Status.ACTIVE
            ).count()
            lines.append(f"Active class schedules: {active_schedules}.")
            lines.append("Today's classes: " + _today_classes(gym))
        if "packages" in visible:
            lines.append(
                "Service packages: "
                + _labeled(
                    ServicePackage.objects.filter(gym=gym)
                    .order_by("name")
                    .values_list("name", "type", "session_count", "price", "currency")[:_NAME_LIMIT]
                )
            )
            lines.append("Today's PT sessions: " + _today_pt(gym))
        if "assignments" in visible:
            active_assignments = TrainerAssignment.objects.filter(
                gym=gym, status=TrainerAssignment.Status.ACTIVE
            ).count()
            lines.append(f"Active trainer assignments: {active_assignments}.")
        if "pt_sales" in visible:
            lines.append("PT sales by status: " + _status_counts(PTSale.objects.filter(gym=gym)))
        if "payments" in visible:
            lines.append("Payments by status: " + _status_counts(Payment.objects.filter(gym=gym)))
        if "expenses" in visible:
            lines.append("Expenses by status: " + _status_counts(Expense.objects.filter(gym=gym)))
            lines.append(
                "Expense categories: "
                + _labeled(
                    ExpenseCategory.objects.filter(gym=gym, is_active=True)
                    .order_by("name")
                    .values_list("name", "code")[:_NAME_LIMIT]
                )
            )
        if "salaries" in visible:
            lines.append(
                "Salary periods by status: " + _status_counts(SalaryPeriod.objects.filter(gym=gym))
            )
        if "retail" in visible:
            lines.append(
                "Retail sales by status: " + _status_counts(RetailSale.objects.filter(gym=gym))
            )
        if "inventory" in visible:
            lines.append("Products: " + _products(gym))
        if "cash" in visible:
            lines.append(
                "Cash sessions by status: " + _status_counts(CashSession.objects.filter(gym=gym))
            )
        if "announcements" in visible:
            published = Announcement.objects.filter(
                gym=gym, status=Announcement.Status.PUBLISHED
            ).count()
            lines.append(f"Published announcements: {published}.")
    return "\n".join(lines)


def _settings(gym: Gym) -> list[str]:
    settings = GymSettings.objects.filter(gym=gym).first()
    if settings is None:
        return ["Settings: not stored yet."]
    approval = "required" if settings.require_separate_finance_approval else "not required"
    consume = "consumes" if settings.class_no_show_consumes_session else "does not consume"
    return [
        (
            f"Settings: member numbers start {settings.member_number_prefix}. "
            f"Finance approval is {approval}. "
            f"Class cancellation cutoff {settings.class_cancellation_cutoff_minutes} minutes. "
            f"A class no-show {consume} a session. "
            f"Membership expiry warnings on days {settings.membership_expiry_warning_days}. "
            f"Expiry email is {'on' if settings.membership_expiry_email_enabled else 'off'}. "
            f"Expiry push is {'on' if settings.membership_expiry_push_enabled else 'off'}."
        )
    ]


def _status_counts(queryset: QuerySet[Any]) -> str:
    rows = queryset.values("status").annotate(total=Count("id")).order_by("status")
    text = ", ".join(f"{row['status']} {row['total']}" for row in rows)
    return text or "none"


def _plans(gym: Gym) -> str:
    rows = (
        MembershipPlan.objects.filter(gym=gym)
        .order_by("code")
        .values_list(
            "code",
            "name",
            "is_active",
            "duration_value",
            "duration_unit",
            "price",
            "currency",
        )[:_NAME_LIMIT]
    )
    return _labeled(
        (
            code,
            name,
            "ACTIVE" if active else "INACTIVE",
            f"{duration} {unit}",
            f"{price} {currency}",
        )
        for code, name, active, duration, unit, price, currency in rows
    )


def _products(gym: Gym) -> str:
    rows = (
        Product.objects.filter(gym=gym, is_active=True)
        .annotate(
            on_hand=Coalesce(
                Sum("stock_movements__quantity", filter=Q(stock_movements__gym_id=gym.id)),
                Value(Decimal("0.000")),
                output_field=_QTY,
            )
        )
        .order_by("name")
        .values_list("name", "sku", "on_hand")[:_NAME_LIMIT]
    )
    return _labeled(rows)


def _staff(gym: Gym, *statuses: str) -> str:
    return _labeled(
        StaffProfile.objects.filter(gym=gym, status__in=statuses)
        .order_by("display_name")
        .values_list("staff_code", "display_name", "status", "employment_title")[:_NAME_LIMIT]
    )


def _today_classes(gym: Gym) -> str:
    _start, _end, day = _local_day(gym)
    rows = (
        ClassOccurrence.objects.filter(gym=gym, local_date=day)
        .exclude(status=ClassOccurrence.Status.CANCELLED)
        .select_related("class_type", "trainer_staff_profile")
        .order_by("starts_at")[:_NAME_LIMIT]
    )
    shown = [
        (
            f"{row.trainer_staff_profile.display_name} {row.class_type.name} "
            f"{_clock(row.starts_at, gym)} {row.status}"
        )
        for row in rows
    ]
    return ", ".join(shown) or "none"


def _today_pt(gym: Gym) -> str:
    start, end, _day = _local_day(gym)
    rows = (
        PTBooking.objects.filter(gym=gym, starts_at__gte=start, starts_at__lt=end)
        .exclude(status=PTBooking.Status.CANCELLED)
        .select_related("trainer_staff_profile", "member")
        .order_by("starts_at")[:_NAME_LIMIT]
    )
    shown = []
    for row in rows:
        member = f"{row.member.first_name} {row.member.last_name}".strip()
        trainer = row.trainer_staff_profile.display_name
        clock = _clock(row.starts_at, gym)
        shown.append(f"{trainer} {member} {clock} {row.status}")
    return ", ".join(shown) or "none"


def _local_day(gym: Gym) -> tuple[datetime, datetime, date]:
    try:
        zone = ZoneInfo(gym.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    start = datetime.combine(timezone.now().astimezone(zone).date(), time.min, tzinfo=zone)
    return start, start + timedelta(days=1), start.date()


def _clock(moment: datetime, gym: Gym) -> str:
    start, _end, _day = _local_day(gym)
    zone = start.tzinfo
    if zone is None:
        return moment.strftime("%H:%M")
    return moment.astimezone(zone).strftime("%H:%M")


def _check_ins_today(gym: Gym) -> int:
    start, end, _day = _local_day(gym)
    return CheckIn.objects.filter(
        gym=gym,
        check_in_at__gte=start,
        check_in_at__lt=end,
    ).count()


def _labeled(rows: Iterable[tuple[object, ...]]) -> str:
    shown = []
    for row in rows:
        parts = [str(part).strip() for part in row if str(part).strip()]
        if parts:
            shown.append(" ".join(parts))
    return ", ".join(shown) or "none"


_READ_DOMAINS = {
    "members": frozenset({"search_members", "get_member"}),
    "plans": frozenset({"search_membership_plans"}),
    "memberships": frozenset({"search_memberships", "search_membership_expiries"}),
    "checkins": frozenset({"search_check_ins"}),
    "assignments": frozenset({"search_trainers", "search_trainer_assignments"}),
    "pt_sales": frozenset({"search_pt_sales", "search_pt_bookings", "search_pt_trainer_sales"}),
    "packages": frozenset({"search_pt_packages", "search_service_packages"}),
    "classes": frozenset(
        {"search_class_occurrences", "search_class_types", "search_class_schedules"}
    ),
    "inventory": frozenset(
        {
            "search_products",
            "search_stock_movements",
            "search_low_stock",
            "search_product_sales",
        }
    ),
    "staff": frozenset({"search_staff", "search_staff_access"}),
    "roles": frozenset({"search_roles", "search_permissions"}),
    "bookings": frozenset({"search_bookings"}),
    "sales": frozenset({"search_sales"}),
    "expenses": frozenset({"search_expenses"}),
    "salary": frozenset({"search_salary_periods"}),
    "cash": frozenset({"search_cash_sessions"}),
    "commissions": frozenset({"search_pt_commissions"}),
    "payments": frozenset({"search_payments", "get_payment"}),
    "announcements": frozenset({"search_announcements"}),
    "reports": frozenset({"get_gym_dashboard", "get_gym_report", "get_trainer_dashboard"}),
}
_WRITE_ONLY_DOMAINS = frozenset({"settings"})
_UNSUPPORTED_DOMAINS = frozenset({"audit", "mail", "files", "passwords", "notifications", "roster"})
_GAP_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("roster", ("shift roster", "daily shift", "အဆိုင်း")),
    ("audit", ("audit log", "audit trail", "စစ်ဆေးမှတ်တမ်း")),
    ("passwords", ("password", "စကားဝှက်")),
    ("mail", ("send mail", "send email", "အီးမေးလ်ပို့", "မေးလ်ပို့")),
    ("files", ("upload a file", "upload file", "ဖိုင်တင်")),
    ("notifications", ("push notification", "အကြောင်းကြားစာ")),
)
_LOOKUP_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("checkins", ("check-in", "check in", "checkins", "attendance", "ဝင်ရောက်", "တက်ရောက်")),
    ("payments", ("payment", "refund", "ငွေပေးချေ", "ပြန်အမ်း")),
    ("plans", ("membership plan", "plan catalog", "plan code", "အစီအစဉ်")),
    ("memberships", ("membership", "အသင်းဝင်")),
    ("packages", ("package", "remaining session", "ပက်ကေ့", "လက်ကျန်အကြိမ်")),
    ("bookings", ("booking", "booked", "who attended", "class attendance", "ဘိုကင်")),
    ("classes", ("class", "အတန်း")),
    ("pt_sales", ("pt sale", "pt sales")),
    ("sales", ("retail sale", "sale", "အရောင်း")),
    ("expenses", ("expense", "အသုံးစရိတ်")),
    ("salary", ("salary", "လစာ")),
    ("cash", ("cash session", "cashbook", "ငွေသား")),
    ("commissions", ("commission", "ကော်မရှင်")),
    ("inventory", ("stock", "on hand", "sku", "လက်ကျန်")),
    ("roles", ("permission", "ခွင့်ပြုချက်", "role")),
    ("staff", ("staff", "ဝန်ထမ်း")),
    ("members", ("member", "အဖွဲ့ဝင်")),
)
_LIMITS = {
    "roster": (
        "A daily shift roster is not available.",
        "နေ့စဉ်အဆိုင်းဇယား မရှိပါ။",
    ),
    "audit": (
        "Audit records are not available.",
        "စစ်ဆေးမှတ်တမ်းကို ကျွန်တော် မဖတ်နိုင်ပါ။",
    ),
    "passwords": (
        "Passwords and sign-in secrets are not available.",
        "စကားဝှက်ကို ကျွန်တော် မဖတ်နိုင်ပါ။",
    ),
    "mail": (
        "Sending mail is not available.",
        "မေးလ်ပို့ခြင်းကို ကျွန်တော် မလုပ်နိုင်ပါ။",
    ),
    "files": (
        "Files are not available.",
        "ဖိုင်ကို ကျွန်တော် မဖတ်နိုင်ပါ။",
    ),
    "notifications": (
        "Push notifications are not available.",
        "အကြောင်းကြားစာ ပို့ခြင်းကို ကျွန်တော် မလုပ်နိုင်ပါ။",
    ),
}
_FIELD_QUESTIONS = {
    "member_id": ("Which member?", "ဘယ်အဖွဲ့ဝင်လဲ။"),
    "trainer_staff_profile_id": ("Which trainer?", "ဘယ်သင်တန်းဆရာလဲ။"),
    "plan_id": ("Which plan?", "ဘယ်အစီအစဉ်လဲ။"),
    "service_package_id": ("Which package?", "ဘယ်ပက်ကေ့လဲ။"),
    "membership_id": ("Which membership?", "ဘယ်အသင်းဝင်ကြေးလဲ။"),
    "payment_id": ("Which payment?", "ဘယ်ငွေပေးချေမှုလဲ။"),
    "class_occurrence_id": ("Which class?", "ဘယ်အတန်းလဲ။"),
    "product_id": ("Which product?", "ဘယ်ပစ္စည်းလဲ။"),
    "amount": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "gross_fee": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "price": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "opening_cash": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "service_date": ("Which date?", "ဘယ်ရက်လဲ။"),
    "effective_from": ("Which date?", "ဘယ်ရက်လဲ။"),
    "start_date": ("Which start date?", "ဘယ်ရက်က စမလဲ။"),
    "business_date": ("Which date?", "ဘယ်ရက်လဲ။"),
    "starts_at": ("Which date and time?", "ရက်နဲ့ အချိန်ကို ပြောပေးပါ။"),
    "ends_at": ("Which date and time?", "ရက်နဲ့ အချိန်ကို ပြောပေးပါ။"),
    "lines": ("Which products?", "ဘယ်ပစ္စည်းတွေလဲ။"),
    "purpose": ("What is the payment for?", "ငွေပေးချေမှုက ဘာအတွက်လဲ။"),
    "quantity": ("What quantity?", "အရေအတွက် ဘယ်လောက်လဲ။"),
}
_GATE_SENTENCES = {
    "rejected": ("That change was not saved.", "ဒီပြောင်းလဲမှုကို မသိမ်းပါ။"),
    "invalid": ("The stored change was not accepted.", "သိမ်းထားတဲ့ ပြောင်းလဲမှုကို လက်မခံပါ။"),
    "assignment": (
        "The trainer must be assigned to the member on that date before this change.",
        "ဒီရက်မှာ သင်တန်းဆရာကို အဖွဲ့ဝင်နဲ့ တာဝန်မပေးရသေးပါ။",
    ),
    "package": (
        "That booking needs an active PT package with sessions left.",
        "ဒီဘိုကင်အတွက် လက်ကျန်ရှိတဲ့ PT ပက်ကေ့ လိုပါတယ်။",
    ),
    "package_required": (
        "That booking needs an active PT package with sessions left. A posted PT sale comes first.",
        "ဒီဘိုကင်အတွက် လက်ကျန်ရှိတဲ့ PT ပက်ကေ့ လိုပါတယ်။ PT အရောင်းကို အရင်တင်ပါ။",
    ),
    "class_package": (
        "That class needs an active class package with sessions left.",
        "ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။",
    ),
    "class_package_required": (
        "That class needs an active class package with sessions left.",
        "ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။",
    ),
    "member_inactive": ("That member is not active.", "ဒီအဖွဲ့ဝင်က အသုံးမပြုနိုင်ပါ။"),
    "plan_inactive": ("That plan is not active.", "ဒီအစီအစဉ်က အသုံးမပြုနိုင်ပါ။"),
    "membership_overlap": (
        "Those dates overlap another membership.",
        "ဒီရက်တွေက အခြားအသင်းဝင်ကြေးနဲ့ ထပ်နေပါတယ်။",
    ),
    "membership_required": ("Which membership?", "ဘယ်အသင်းဝင်ကြေးလဲ။"),
    "membership_mismatch": (
        "That membership does not belong to this member.",
        "ဒီအသင်းဝင်ကြေးက ဒီအဖွဲ့ဝင်ရဲ့ မဟုတ်ပါ။",
    ),
    "amount_unconfirmed": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "amount_mismatch": (
        "That amount is not the amount you said.",
        "ပြောထားတဲ့ ပမာဏနဲ့ မတူပါ။",
    ),
    "currency_mismatch": (
        "Use the gym currency.",
        "အားကစားခန်းမရဲ့ ငွေကြေးကို သုံးပါ။",
    ),
    "package_unavailable": (
        "That package is not available for this sale. Which package?",
        "ဒီပက်ကေ့ကို အရောင်းအတွက် မသုံးနိုင်ပါ။ ဘယ်ပက်ကေ့လဲ။",
    ),
    "balance_exceeded": (
        "That amount is more than the outstanding balance.",
        "ပမာဏက ပေးရန်ကျန်ငွေထက် များနေပါတယ်။",
    ),
    "class_full": (
        "That class has no remaining places.",
        "ဒီအတန်းမှာ နေရာမကျန်ပါ။",
    ),
    "duplicate_booking": (
        "That member already has a booking for this class.",
        "ဒီအဖွဲ့ဝင်က ဒီအတန်းမှာ ဘိုကင်ရှိပြီးပါပြီ။",
    ),
    "schedule_conflict": (
        "That time is already booked.",
        "ဒီအချိန်မှာ ဘိုကင်ရှိပြီးပါပြီ။",
    ),
    "trainer_unavailable": (
        "That trainer is already booked at that time.",
        "ဒီသင်တန်းဆရာက ဒီအချိန်မှာ ဘိုကင်ရှိပြီးပါပြီ။",
    ),
    "time_order": (
        "The end time has to be after the start time.",
        "အဆုံးအချိန်က စတင်အချိန်ထက် နောက်ကျရပါမယ်။",
    ),
    "stock_short": (
        "There is not enough stock for that quantity.",
        "တောင်းတဲ့ အရေအတွက်အတွက် လက်ကျန်မလုံလောက်ပါ။",
    ),
    "product_inactive": ("That product is not active.", "ဒီပစ္စည်းက အသုံးမပြုနိုင်ပါ။"),
    "quantity_invalid": (
        "Tell me a positive whole-number quantity.",
        "အရေအတွက်ကို ကိန်းပြည့် အပေါင်းနဲ့ ပြောပေးပါ။",
    ),
    "quantity_unconfirmed": ("What quantity?", "အရေအတွက် ဘယ်လောက်လဲ။"),
    "duplicate_product": (
        "Each product can be on the sale once.",
        "ပစ္စည်းတစ်ခုကို တစ်ကြောင်းပဲ ထည့်ပါ။",
    ),
    "payment_unconfirmed": ("Which payment?", "ဘယ်ငွေပေးချေမှုလဲ။"),
    "payment_state": (
        "That payment cannot be changed in its current state.",
        "ဒီငွေပေးချေမှုကို အခုအခြေအနေမှာ ပြင်လို့ မရပါ။",
    ),
    "retail_charge": (
        "Use the retail return for that product sale.",
        "ဒီပစ္စည်းအရောင်းကို ပြန်အမ်းလမ်းကြောင်းကနေ လုပ်ပါ။",
    ),
    "refund_exceeds": (
        "That refund is more than the refundable balance.",
        "ပြန်အမ်းငွေက ပြန်အမ်းနိုင်တဲ့ ပမာဏထက် များနေပါတယ်။",
    ),
    "void_blocked": (
        "That payment cannot be fully reversed.",
        "ဒီငွေပေးချေမှုကို အပြည့်အဝ ပြန်လှန်လို့ မရပါ။",
    ),
    "date_invalid": ("That date cannot be used.", "ဒီရက်ကို အသုံးမပြုနိုင်ပါ။"),
    "unmetered_denied": (
        "An unmetered booking is not allowed for this account.",
        "အထုပ်မဲ့ ဘိုကင်လုပ်ခွင့် မရှိပါ။",
    ),
    "unmetered_reason": (
        "Tell me the reason for the unmetered booking.",
        "အထုပ်မဲ့ ဘိုကင်အတွက် အကြောင်းပြချက် ပြောပေးပါ။",
    ),
    "missing_record": (
        "That record was not found in this gym.",
        "ဒီအားကစားခန်းမမှာ မှတ်တမ်း မတွေ့ပါ။",
    ),
    "check_in_ineligible": (
        "This member does not have an eligible active membership.",
        "ဒီအဖွဲ့ဝင်မှာ ဝင်ရောက်နိုင်တဲ့ အသင်းဝင်ကြေး မရှိပါ။",
    ),
}
_WRITE_WORDS = re.compile(
    r"\b(add|create|book|register|update|cancel|void|approve|reject|sell|"
    r"archive|restore|submit|adjust|reconcile|grant)\b",
    re.IGNORECASE,
)
_WRITE_PHRASES = (
    "record a ",
    "record the ",
    "လုပ်ပေး",
    "ဖွင့်ချင်",
    "ဖွင့်ပေး",
    "တပ်ပေး",
    "ထည့်ပေး",
    "မှတ်ပေး",
    "ပယ်ဖျက်",
    "ပြင်ပေး",
    "စာရင်းသွင်း",
    "ရောင်းပေး",
    "အတည်ပြု",
    "ဘိုကင်လုပ်",
    "တာဝန်ပေးပါ",
    "ငွေချေ",
)
_EXACT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b[A-Z][A-Z0-9]*-[A-Z0-9]+\b|"
    r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)+\b|\b[A-Z][A-Z0-9]{2,}\b"
)
_EXACT_SKIP = frozenset({"SKU", "PT", "TODAY"})
_MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")
_AMOUNT = re.compile(r"\d+(?:\.\d+)?")
_SUCCESS = re.compile(
    r"(\bis saved\b|\bhas been saved\b|\bwas saved\b|\bsuccessfully\b|"
    r"\bi saved\b|\bi've saved\b|\bi have saved\b|"
    r"\bregistered\b|\bcreated\b|\bcompleted\b|"
    r"သိမ်းပြီး|ပြီးပါပြီ|အောင်မြင်ပါပြီ|အောင်မြင်စွာ|မှတ်ပြီးပါပြီ|ပါပြီ)",
    re.IGNORECASE,
)
_LEAK_TEXT = (
    "traceback",
    "operationalerror",
    "integrityerror",
    "psycopg",
    "sqlstate",
    "django.db",
    "permission_denied",
    "unknown_tool",
    "invalid_arguments",
    "not_a_read",
    "step_limit",
    "stack trace",
    "select *",
)
_ACTIVITY_STATUS = re.compile(r"^[a-z_]{1,40}$")
_CLAIM_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CLAIM_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")
_CLAIM_CODE = re.compile(r"\b[A-Z][A-Z0-9]*-[A-Z0-9]+\b")
_CLAIM_NAME = re.compile(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)+\b")
_STATUSES = (
    "PARTIALLY_REFUNDED",
    "PENDING_APPROVAL",
    "SALE_RETURN",
    "ADJUSTMENT",
    "CORRECTION",
    "SUSPENDED",
    "CANCELLED",
    "ARCHIVED",
    "SCHEDULED",
    "PUBLISHED",
    "EXHAUSTED",
    "PROMOTION",
    "REVERSAL",
    "NO_SHOW",
    "REJECTED",
    "REFUNDED",
    "ATTENDED",
    "PURCHASE",
    "OPENING",
    "EXPIRED",
    "PARTIAL",
    "PENDING",
    "GENERAL",
    "DAMAGE",
    "ACTIVE",
    "BOOKED",
    "CHARGE",
    "CLOSED",
    "DRAFT",
    "EVENT",
    "LOSS",
    "PAID",
    "POSTED",
    "REFUND",
    "UNPAID",
    "VOID",
    "OPEN",
    "SALE",
)
_STATUS_RE = re.compile(r"\b(?:" + "|".join(_STATUSES) + r")\b")
_ROW_SKIP = frozenset(
    {
        "id",
        "has_more",
        "next_after",
        "ambiguous",
        "phone",
        "email",
        "notes",
        "address",
        "secret",
        "password",
        "traceback",
        "sql",
    }
)
_NOTICE_ERRORS = frozenset(
    {
        "permission_denied",
        "unavailable",
        "date_unconfirmed",
        "date_ambiguous",
        "time_unconfirmed",
        "missing_fields",
        "ambiguous",
        "not_found",
        "already_saved",
        *_GATE_SENTENCES,
    }
)


def knowledge_capability(domain: str) -> str:
    """Return read, write_only, or unsupported for one assistant domain."""

    if domain in _READ_DOMAINS:
        return "read"
    if domain in _WRITE_ONLY_DOMAINS:
        return "write_only"
    if domain in _UNSUPPORTED_DOMAINS:
        return "unsupported"
    raise KeyError(domain)


_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def shown_day(value: str) -> str:
    """Return a calendar day as 13 November 2026 (2026-11-13)."""

    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return value.strip()
    return f"{day.day} {_MONTH_NAMES[day.month - 1]} {day.year} ({day.isoformat()})"


def speaks_burmese(text: str) -> bool:
    """Return whether the staff message uses Myanmar script."""

    return any("\u1000" <= char <= "\u109f" for char in text)


def stated_amount(text: str) -> str | None:
    """Return the Arabic form of a stated amount. The value is unchanged."""

    converted = text.translate(_MYANMAR_DIGITS).replace(",", "").replace("၊", "").replace(" ", "")
    match = _AMOUNT.search(converted)
    if match is None:
        return None
    return match.group(0)


def exact_code(text: str) -> str:
    """Translate Myanmar digits in a code without adding or dropping any."""

    return text.translate(_MYANMAR_DIGITS)


def unsupported_lookup_reply(text: str) -> str | None:
    """Return a limitation when the staff asked for a record no read can fetch."""

    if _is_change_request(text):
        return None
    domain = _record_gap(text)
    if domain is None:
        return None
    return _with_exact(_sentence(_LIMITS[domain], text), text)


def information_question(text: str) -> bool:
    """A question about stored records, not a request to change them."""

    if _is_change_request(text) or short_reply_kind(text) or _states_intent(text):
        return False
    if _asks(text):
        folded = text.casefold()
        names_member = "member" in folded or "အဖွဲ့ဝင်" in text
        names_class = bool(re.search(r"\bclass\b", folded) or "အတန်း" in text)
        names_pt = bool(re.search(r"(?<![a-z0-9])pt(?![a-z0-9])", folded) or "နည်းပြ" in text)
        if names_member and (names_class or names_pt) and not _stored_lookup(text):
            return False
        return True
    return _lookup_domain(text) is not None


def fact_tools(text: str) -> tuple[str, ...]:
    """Read tools a factual question needs. A list still uses listing_tools."""

    if not information_question(text):
        return ()
    folded = text.casefold()
    tools: list[str] = []
    if _mentions_plan(text):
        tools.append("search_membership_plans")
    if _mentions_package_catalog(text):
        tools.append("search_service_packages")
    if _mentions_granted_package(text):
        tools.append("search_pt_packages")
    if _dashboard_question(text) or _member_count_question(text):
        tools.append("get_gym_dashboard")
    if _mentions_class_schedule(text):
        tools.append("search_class_occurrences")
    if (
        "booking" in folded
        or "ဘိုကင်" in text
        or "who attended" in folded
        or "class attendance" in folded
    ):
        tools.append("search_bookings")
    if "expense" in folded or "အသုံးစရိတ်" in text or "ကုန်ကျ" in text:
        tools.append("search_expenses")
    if "cash" in folded or "cashbook" in folded or "ငွေသား" in text:
        tools.append("search_cash_sessions")
    if "announcement" in folded or "ကြေညာ" in text:
        tools.append("search_announcements")
    if "salary" in folded or "လစာ" in text:
        tools.append("search_salary_periods")
    if ("retail" in folded or "အရောင်း" in text) and not _mentions_product_rank(text):
        if not _mentions_pt_sale(text):
            tools.append("search_sales")
    if ("payment" in folded or "ငွေပေး" in text or "receipt" in folded) and not _mentions_plan(text):
        tools.append("search_payments")
    if "commission" in folded or "ကော်မရှင်" in text:
        tools.append("search_pt_commissions")
    if _mentions_expiry(text):
        tools.append("search_membership_expiries")
    if _mentions_checkin(text):
        tools.append("search_check_ins")
    if _mentions_assignment(text):
        tools.append("search_trainer_assignments")
    elif _mentions_trainer_list(text):
        tools.append("search_trainers")
    if _mentions_pt_sale(text):
        tools.append("search_pt_sales")
    if _mentions_pt_booking(text):
        tools.append("search_pt_bookings")
    if _mentions_class_type(text):
        tools.append("search_class_types")
    if _mentions_schedule(text):
        tools.append("search_class_schedules")
    if _mentions_low_stock(text):
        tools.append("search_low_stock")
    elif _mentions_stock_movement(text):
        tools.append("search_stock_movements")
    elif _mentions_product(text) and not _mentions_product_rank(text):
        tools.append("search_products")
    if _mentions_product_rank(text):
        tools.append("search_product_sales")
    if _mentions_staff_access(text):
        tools.append("search_staff_access")
    elif _mentions_role(text):
        tools.append("search_roles")
    elif _mentions_staff(text):
        tools.append("search_staff")
    if (
        ("membership" in folded or "အသင်းဝင်" in text)
        and not _mentions_plan(text)
        and not _mentions_expiry(text)
    ):
        tools.append("search_memberships")
    return tuple(dict.fromkeys(tools))


def grounded_information_reply(reply: str, *, brief: str, staff_text: str) -> str:
    """Correct unsupported details. Use the brief only when nothing valid remains."""

    checked = grounded_read_reply(
        reply,
        results=(),
        brief=brief,
        staff_text=staff_text,
        fallback=False,
    )
    if checked.strip():
        return checked
    lines = _relevant_brief_lines(brief, staff_text)
    if lines:
        return " ".join(lines)[:1500]
    return grounded_read_reply(reply, results=(), brief=brief, staff_text=staff_text)


def refuse_write_for_lookup(text: str, operation: str) -> bool:
    """A lookup must not be answered by proposing a write."""

    if operation != "write" or _is_change_request(text):
        return False
    if short_reply_kind(text):
        return False
    if information_question(text):
        return True
    return _lookup_domain(text) is not None


_AFFIRMATIONS = frozenset({"ထည့်မယ်", "အင်း", "ဟုတ်တယ်", "ရတယ်", "လုပ်ပါ", "yes", "yeah", "ok", "okay"})
_DECLINES = frozenset({"မထည့်တော့ဘူး", "မလုပ်တော့ဘူး", "မဟုတ်ဘူး"})
_OFFERED_OPERATIONS = (
    ("create_pt_sale", ("ပက်ကေ့", "package")),
    ("create_pt_booking", ("ဘိုကင်", "booking")),
    ("create_assignment", ("တာဝန်ပေး", "assignment")),
    ("create_member", ("အဖွဲ့ဝင်အသစ်", "အဖွဲ့ဝင် အသစ်", "new member")),
    ("create_membership", ("အသင်းဝင်", "membership")),
    ("create_payment", ("ငွေပေး", "payment")),
)


def short_reply_kind(text: str) -> str:
    """Return affirm, decline, or empty for a reply that only continues the last question."""

    token = "".join(text.strip().strip("။.!?၊ ").split())
    folded = token.casefold()
    if token in _DECLINES or folded in _DECLINES:
        return "decline"
    if token in _AFFIRMATIONS or folded in _AFFIRMATIONS:
        return "affirm"
    return ""


def offered_operations(question: str) -> tuple[str, ...]:
    """Return the distinct operations named by the immediately previous question."""

    folded = question.casefold()
    found: list[str] = []
    for tool, words in _OFFERED_OPERATIONS:
        if any(word in question or word in folded for word in words):
            found.append(tool)
    return tuple(found)


def operation_workflow(tool: str) -> str:
    """Return the workflow card for one offered operation."""

    if tool in {"create_pt_sale", "create_pt_booking", "create_assignment"}:
        return "pt"
    if tool == "create_member":
        return "member"
    if tool == "create_membership":
        return "membership"
    if tool == "create_payment":
        return "payment"
    return ""


def gate_sentence(
    code: str,
    staff_text: str,
    detail: Mapping[str, object] | None = None,
) -> str:
    """Return the staff sentence for a known validation result."""

    pair = _GATE_SENTENCES.get(code)
    if pair is None:
        pair = _GATE_SENTENCES["invalid"]
    sentence = _sentence(pair, staff_text)
    if not detail:
        return sentence
    extra: list[str] = []
    plan = detail.get("plan")
    start = detail.get("start")
    end = detail.get("end")
    balance = detail.get("balance")
    if isinstance(plan, str) and plan.strip():
        extra.append(plan.strip())
    if isinstance(start, str) and isinstance(end, str) and start and end:
        extra.append(f"{start} - {end}")
    if isinstance(balance, str) and balance.strip():
        extra.append(balance.strip())
    if not extra:
        return sentence
    return sentence + " " + " ".join(extra)


def staff_notice(body: Mapping[str, object], staff_text: str) -> str | None:
    """Return the staff sentence for a tool outcome that must not be invented."""

    error = body.get("error")
    if not isinstance(error, str):
        status = body.get("status")
        error = status if isinstance(status, str) else ""
    if error not in _NOTICE_ERRORS:
        return None
    burmese = speaks_burmese(staff_text)
    if error == "permission_denied":
        return "ဒီအချက်အလက်ကို ကြည့်ခွင့် မရှိပါ။" if burmese else "You cannot view that information."
    if error == "unavailable":
        return "မပြီးမြောက်ပါ။ ဘာမှ မပြောင်းရသေးပါ။" if burmese else "I could not complete that."
    if error == "not_found":
        return "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။" if burmese else "No stored record matches that."
    if error == "already_saved":
        return "ဒီပြောင်းလဲမှု ရှိပြီးသားပါ။" if burmese else "That change is already stored."
    if error == "date_unconfirmed":
        return "ဘယ်ရက်လဲ။" if burmese else "Which date?"
    if error in {"date_invalid", "date_ambiguous"}:
        return _date_problem_sentence(body, burmese, invalid=error == "date_invalid")
    if error == "time_unconfirmed":
        return "အချိန် ဘယ်လောက်လဲ။" if burmese else "What time?"
    if error == "missing_fields":
        return _missing_question(body.get("fields"), burmese)
    if error == "ambiguous":
        field = body.get("field")
        question = ""
        if isinstance(field, str):
            pair = _FIELD_QUESTIONS.get(field)
            if pair is not None:
                question = pair[1] if burmese else pair[0]
        choice = _ambiguous_question(body.get("matches"), burmese)
        if question and question not in choice:
            return f"{question} {choice}".strip()
        return choice
    if error in _GATE_SENTENCES:
        return gate_sentence(error, staff_text, body)
    return None


def lookup_refusal(staff_text: str) -> str:
    """Tell the staff a write was not used to answer a lookup."""

    if speaks_burmese(staff_text):
        return "ဒါကို ရှာကြည့်လို့ ရပါတယ်။ ဖြေဖို့အတွက် ပြောင်းလဲမှု မလုပ်ပါ။"
    return "I can look that up. I will not make a change to answer it."


def public_tool_label(name: str) -> str:
    """Return the staff label for a tool. The stored name stays unchanged."""

    if name == "assistant":
        return "Assistant"
    from apps.assistant.registry import production_registry

    try:
        tool = production_registry().get(name)
    except KeyError:
        return "Lookup"
    sentence = tool.description.strip().split(".")[0].strip()
    if sentence and name not in sentence and "_" not in sentence:
        return sentence[:120]
    return "Lookup" if tool.operation == "read" else "Change"


def public_activity(name: str, status: str) -> dict[str, str]:
    """Return the activity item the staff API may show."""

    safe = status if _ACTIVITY_STATUS.fullmatch(status) else "completed"
    return {"name": public_tool_label(name), "status": safe}


_LIST_MARKERS = ("search", "list", "show", "which", "find", "စာရင်း", "ရှာ")
_COUNT_MARKERS = ("how many", "count", "ဘယ်နှစ်", "အရေအတွက်")
_TODAY_WORDS = ("today", "tonight", "ဒီနေ့", "ယနေ့")
_STATED_DATE_WORDS = (
    "tomorrow",
    "yesterday",
    "next week",
    "this week",
    "last week",
    "this month",
    "next month",
    "မနက်ဖြန်",
    "မနေ့က",
    "ဒီလ",
    "နောက်လ",
    "နောက်တစ်ပတ်",
)
_CLASS_DATE_TOOLS = frozenset(
    {
        "search_class_occurrences",
        "search_bookings",
        "search_check_ins",
        "search_pt_bookings",
        "search_product_sales",
    }
)
_BUSINESS_DATE_TOOLS = frozenset(
    {
        "search_sales",
        "search_expenses",
        "search_salary_periods",
        "search_cash_sessions",
        "search_stock_movements",
    }
)
_STATUS_TOOLS = frozenset({"search_sales", "search_expenses", "search_salary_periods"})
_STATUS_CODES = {
    "posted": "POSTED",
    "draft": "DRAFT",
    "void": "VOID",
    "voided": "VOID",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "rejected": "REJECTED",
}
_LIST_LABELS = {
    "search_sales": ("Sales.", "အရောင်း။"),
    "search_expenses": ("Expenses.", "အသုံးစရိတ်။"),
    "search_salary_periods": ("Salary periods.", "လစာကာလ။"),
    "search_cash_sessions": ("Cash sessions.", "ငွေသားအစည်းအဝေး။"),
    "search_class_occurrences": ("Class occurrences.", "အတန်းချိန်။"),
    "search_bookings": ("Class bookings.", "အတန်းဘိုကင်။"),
    "search_memberships": ("Memberships.", "အသင်းဝင်မှု။"),
    "search_membership_plans": ("Membership plans.", "အသင်းဝင်အစီအစဉ်။"),
}
_MISSING_LIST = (
    "I could not retrieve those stored records, so I will not guess them.",
    "သိမ်းထားတဲ့ မှတ်တမ်းကို မဖတ်ရသေးလို့ ခန့်မှန်းပြီး မပြောပါ။",
)
_MISSING_REST = (
    "I could not retrieve the other stored records, so I will not guess them.",
    "ကျန်တဲ့ သိမ်းထားတဲ့ မှတ်တမ်းကို မဖတ်ရသေးလို့ ခန့်မှန်းပြီး မပြောပါ။",
)
_PAYMENT_STOP = frozenset(
    {
        "what",
        "did",
        "the",
        "member",
        "pay",
        "payment",
        "paid",
        "a",
        "an",
        "for",
        "this",
        "that",
        "which",
        "how",
        "many",
        "search",
        "list",
        "show",
        "do",
        "not",
        "change",
        "anything",
        "records",
        "tell",
        "me",
    }
)


def unnamed_member_payment_reply(text: str) -> str | None:
    """Ask which member when a payment question does not identify one."""

    if _is_change_request(text) or not _payment_question(text):
        return None
    folded = text.casefold()
    unnamed = (
        "the member" in folded
        or "that member" in folded
        or "this member" in folded
        or "အဲဒီအဖွဲ့ဝင်" in text
        or "ဒီအဖွဲ့ဝင်" in text
    )
    if not unnamed or _identified_member(text):
        return None
    if speaks_burmese(text):
        return "ဘယ်အဖွဲ့ဝင်လဲ။ အဖွဲ့ဝင်နံပါတ် ပြောပါ။"
    return "Which member? Tell me the member number."


def widen_list_filters(
    tool_name: str,
    payload: Mapping[str, object],
    staff_text: str,
    today: date | None,
) -> dict[str, object]:
    """Drop a silent date or status filter on a general list."""

    allowed = set(listing_tools(staff_text)) | set(fact_tools(staff_text))
    if tool_name not in allowed:
        return dict(payload)
    updated = dict(payload)
    scope = _date_scope(staff_text)
    stated = _stated_date(staff_text)
    window = None
    named_day = None
    if today is not None:
        from apps.assistant.lookups import relative_day, relative_window

        window = relative_window(staff_text, today)
        named_day = None if window else relative_day(staff_text, today)
    if tool_name == "search_membership_expiries":
        return _expiry_filters(
            updated,
            staff_text=staff_text,
            today=today,
            scope=scope,
            window=window,
            named_day=named_day,
        )
    if tool_name == "search_pt_sales":
        return _service_date_filters(
            updated,
            today=today,
            scope=scope,
            window=window,
            named_day=named_day,
            stated=stated,
        )
    if tool_name == "search_trainer_assignments":
        return _assignment_filters(updated, staff_text)
    if tool_name == "search_check_ins" and _absent_question(staff_text):
        return _absence_filters(updated, today=today, window=window, named_day=named_day)
    if tool_name == "search_bookings" and _attended(staff_text):
        updated["status"] = "ATTENDED"
    if tool_name in _CLASS_DATE_TOOLS:
        if window is not None:
            updated["date_from"] = window[0].isoformat()
            updated["date_to"] = window[1].isoformat()
        elif scope == "open":
            updated.pop("date_from", None)
            updated.pop("date_to", None)
        elif scope == "today" and today is not None:
            updated["date_from"] = today.isoformat()
            updated["date_to"] = today.isoformat()
        elif named_day is not None:
            updated["date_from"] = named_day.isoformat()
            updated["date_to"] = named_day.isoformat()
        elif stated is not None:
            updated["date_from"] = stated
            updated["date_to"] = stated
        elif (
            tool_name == "search_class_occurrences" and today is not None and _upcoming(staff_text)
        ):
            updated["date_from"] = today.isoformat()
            updated["date_to"] = (today + timedelta(days=30)).isoformat()
    if tool_name in _BUSINESS_DATE_TOOLS:
        if scope == "open":
            updated.pop("business_date", None)
        elif scope == "today" and today is not None:
            updated["business_date"] = today.isoformat()
        elif named_day is not None:
            updated["business_date"] = named_day.isoformat()
        elif stated is not None:
            updated["business_date"] = stated
    if tool_name == "search_cash_sessions":
        cash = _cash_scope(staff_text)
        if cash == "all":
            updated.pop("status", None)
        elif cash == "open":
            updated["status"] = "OPEN"
        else:
            updated["status"] = "CLOSED"
    elif tool_name in _STATUS_TOOLS:
        named = _named_statuses(staff_text)
        if len(named) == 1:
            updated["status"] = named[0]
        else:
            updated.pop("status", None)
    return updated


def listing_tools(text: str) -> tuple[str, ...]:
    """Read tools a general list must use. A count can stay on the brief."""

    folded = text.casefold()
    if _dashboard_question(text):
        return ("get_gym_dashboard",)
    tools: list[str] = []
    if _announcement_question(text):
        tools.append("search_announcements")
    if not _is_listing(text):
        return tuple(tools)
    if (
        "membership plan" in folded
        or "plan catalog" in folded
        or "plan named" in folded
        or ("named " in folded and "plan" in folded)
        or "အစီအစဉ်" in text
    ):
        tools.append("search_membership_plans")
    if "memberships" in folded or "a membership" in folded:
        tools.append("search_memberships")
    if "occurrence" in folded or re.search(r"\bclasses\b", folded) or "အတန်း" in text:
        tools.append("search_class_occurrences")
    if "booking" in folded or "ဘိုကင်" in text:
        tools.append("search_bookings")
    if "pt sale" not in folded and (
        "retail" in folded or re.search(r"\bsales?\b", folded) or "အရောင်း" in text
    ):
        tools.append("search_sales")
    if "expense" in folded or "အသုံးစရိတ်" in text or "ကုန်ကျစရိတ်" in text:
        tools.append("search_expenses")
    if "salary" in folded or "လစာ" in text:
        tools.append("search_salary_periods")
    if "cash session" in folded or "ငွေသား" in text:
        tools.append("search_cash_sessions")
    return tuple(dict.fromkeys(tools))


def complete_stored_reply(
    staff_text: str,
    reads: Mapping[str, Mapping[str, object]],
) -> str | None:
    """Return a list answer only when the server will show that text in full.

    A missing read stays None so the model can still call that tool. This does
    not decide a write.
    """

    required = listing_tools(staff_text)
    if required and not set(required).issubset(reads):
        return None
    if not required and _ambiguous_member_reply(staff_text, reads) is None:
        return None
    return stored_listing_reply(staff_text, reads)


def stored_listing_reply(
    staff_text: str,
    reads: Mapping[str, Mapping[str, object]],
) -> str | None:
    """Answer a list from read results, or refuse to guess when the read is missing."""

    member = _ambiguous_member_reply(staff_text, reads)
    if member is not None:
        return member
    tools = listing_tools(staff_text)
    if not tools:
        return None
    chunks: list[str] = []
    missing = False
    for tool in tools:
        result = reads.get(tool)
        if result is None:
            missing = True
            continue
        chunks.append(_listed_chunk(tool, result, staff_text))
    if not chunks:
        return _sentence(_MISSING_LIST, staff_text)
    body = " ".join(chunks)
    note = _limit_note(staff_text)
    if note:
        body = f"{body} {note}"
    if missing:
        body = f"{body} {_sentence(_MISSING_REST, staff_text)}"
    return body


def _is_listing(text: str) -> bool:
    if _is_change_request(text):
        return False
    folded = text.casefold()
    listed = any(marker in folded or marker in text for marker in _LIST_MARKERS)
    counted = any(marker in folded or marker in text for marker in _COUNT_MARKERS)
    return listed and not (counted and not any(marker in folded for marker in ("search", "list")))


def _stated_date(text: str) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match is None:
        return None
    return match.group(0)


def _limit_note(text: str) -> str:
    """Say when a list was intentionally limited to a date or status."""

    parts: list[str] = []
    scope = _date_scope(text)
    stated = _stated_date(text)
    if scope == "today":
        parts.append(
            "ဒီစာရင်းက ဒီနေ့အတွက်ပါ။" if speaks_burmese(text) else "This list is limited to today."
        )
    elif stated is not None:
        parts.append(
            f"ဒီစာရင်းက {stated} အတွက်ပါ။"
            if speaks_burmese(text)
            else f"This list is limited to {stated}."
        )
    cash = _cash_scope(text)
    if "cash session" in text.casefold() or "ငွေသား" in text:
        if cash == "open":
            parts.append("Open sessions only." if not speaks_burmese(text) else "ဖွင့်ထားတဲ့ အစည်းအဝေးပဲ။")
        elif cash == "closed":
            parts.append(
                "Closed sessions only." if not speaks_burmese(text) else "ပိတ်ထားတဲ့ အစည်းအဝေးပဲ။"
            )
    named = _named_statuses(text)
    if len(named) == 1:
        parts.append(
            f"အခြေအနေ {named[0]} ပဲ။" if speaks_burmese(text) else f"Limited to status {named[0]}."
        )
    return " ".join(parts)


def _date_scope(text: str) -> str:
    folded = text.casefold()
    if any(word in folded or word in text for word in _TODAY_WORDS):
        return "today"
    if re.search(r"\d{4}-\d{2}-\d{2}", text):
        return "stated"
    if any(word in folded or word in text for word in _STATED_DATE_WORDS):
        return "stated"
    return "open"


def _named_statuses(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    found: list[str] = []
    for word, code in _STATUS_CODES.items():
        if re.search(rf"\b{word}\b", folded) and code not in found:
            found.append(code)
    return tuple(found)


def _cash_scope(text: str) -> str:
    folded = text.casefold()
    opened = re.search(r"\bopen\b", folded) is not None or "ဖွင့်" in text
    closed = re.search(r"\bclosed\b", folded) is not None or "ပိတ်" in text
    if opened and not closed:
        return "open"
    if closed and not opened:
        return "closed"
    return "all"


def _payment_question(text: str) -> bool:
    folded = text.casefold()
    return "pay" in folded or "payment" in folded or "ငွေပေး" in text


def _identified_member(text: str) -> bool:
    if re.search(r"\bMEM-\d+\b", text, re.IGNORECASE):
        return True
    names = [
        word
        for word in re.findall(r"\b[A-Z][a-z]+\b", text)
        if word.casefold() not in _PAYMENT_STOP
    ]
    return len(names) >= 1


def _ambiguous_member_reply(
    text: str,
    reads: Mapping[str, Mapping[str, object]],
) -> str | None:
    if not _payment_question(text):
        return None
    result = reads.get("search_members")
    if not isinstance(result, Mapping) or not result.get("ambiguous"):
        return None
    rows = result.get("results")
    numbers: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and isinstance(row.get("member_number"), str):
                numbers.append(str(row["member_number"]))
    shown = ", ".join(numbers)
    if speaks_burmese(text):
        return f"အဖွဲ့ဝင် တစ်ခုမက ရှိပါတယ်။ ဘယ်နံပါတ်လဲ။ {shown}".strip()
    return f"Which member? {shown}".strip()


def _listed_chunk(tool: str, result: Mapping[str, object], staff_text: str) -> str:
    if tool == "get_gym_dashboard":
        return _dashboard_listing(result, staff_text)
    if tool == "search_announcements":
        return _announcement_listing(result, staff_text)
    label = _sentence(_LIST_LABELS[tool], staff_text)
    if tool == "search_membership_plans":
        body = _plan_listing(result, staff_text)
    else:
        body = _render_reads((result,), staff_text)
    if result.get("has_more"):
        more = (
            "နောက်ထပ် သိမ်းထားတဲ့ မှတ်တမ်း ရှိသေးတယ်။"
            if speaks_burmese(staff_text)
            else "More stored rows are on the next page."
        )
        body = f"{body} {more}"
    return f"{label} {body}"


def _plan_listing(result: Mapping[str, object], staff_text: str) -> str:
    from apps.assistant.workflow_state import selling_membership

    rows = _mapping_rows(result)
    if selling_membership(staff_text):
        inactive = [row for row in rows if str(row.get("status") or "") == "INACTIVE"]
        rows = [row for row in rows if str(row.get("status") or "") == "ACTIVE"]
        if not rows and inactive:
            if speaks_burmese(staff_text):
                return "ဒီအစီအစဉ်က အသင်းဝင်အသစ်အတွက် အသုံးမပြုနိုင်ပါ။"
            return "That plan is not available for a new membership."
    name = _named_plan(staff_text)
    if name:
        rows = [row for row in rows if str(row.get("name", "")).casefold() == name.casefold()]
        if len(rows) > 1:
            return _ask_plans(rows, staff_text)
    return _render_reads(({"results": rows},), staff_text)


def _dashboard_question(text: str) -> bool:
    if _is_change_request(text):
        return False
    folded = text.casefold()
    if "လက်ကျန်" in text or "on hand" in folded or "sku" in folded:
        return False
    if _mentions_plan(text) or _mentions_package_catalog(text):
        return False
    money = any(
        word in folded or word in text
        for word in ("income", "revenue", "profit", "ဝင်ငွေ", "အမြတ်", "ဘယ်လောက်")
    )
    expense = "expense" in folded or "အသုံး" in text or "ကုန်ကျ" in text
    return (money or expense) and (
        "ဘယ်လောက်" in text or "how much" in folded or "total" in folded or "ဒီလ" in text
    )


def _announcement_question(text: str) -> bool:
    if _is_change_request(text):
        return False
    folded = text.casefold()
    return "announcement" in folded or "ကြေညာ" in text


def _dashboard_listing(result: Mapping[str, object], staff_text: str) -> str:
    period = result.get("range")
    start = ""
    end = ""
    if isinstance(period, Mapping):
        start = str(period.get("date_from") or "")
        end = str(period.get("date_to") or "")
    financial = result.get("financial")
    if not isinstance(financial, Mapping):
        if speaks_burmese(staff_text):
            return "ဒီစာရင်းရဲ့ ငွေကြေးပမာဏကို ကြည့်ခွင့် မရှိပါ။"
        return "You cannot view the financial amounts for that range."
    fields = (
        ("new_member_revenue", "New-member revenue", "အသင်းဝင်အသစ်"),
        ("renewal_revenue", "Renewal revenue", "သက်တမ်းတိုး"),
        ("pt_revenue", "PT revenue", "PT"),
        ("class_revenue", "Class revenue", "အတန်း"),
        ("product_revenue", "Product revenue", "ကုန်ပစ္စည်း"),
        ("other_revenue", "Other revenue", "အခြား"),
        ("total_revenue", "Total revenue", "စုစုပေါင်းဝင်ငွေ"),
        ("total_expenses", "Expenses", "အသုံးစရိတ်"),
        ("net_profit", "Profit", "အမြတ်"),
    )
    parts = [
        f"{burmese if speaks_burmese(staff_text) else english} {financial.get(key)}"
        for key, english, burmese in fields
        if financial.get(key) not in (None, "")
    ]
    shown = "။ ".join(parts) if speaks_burmese(staff_text) else ". ".join(parts)
    if speaks_burmese(staff_text):
        return f"{start} မှ {end}။ {shown}။"
    return f"{start} to {end}. {shown}."


def _announcement_listing(result: Mapping[str, object], staff_text: str) -> str:
    rows = _mapping_rows(result)
    if not rows:
        if speaks_burmese(staff_text):
            return "ကြေညာချက် မရှိပါ။"
        return "No announcements are stored."
    shown = "; ".join(
        " ".join(
            part for part in (str(row.get("title") or ""), str(row.get("status") or "")) if part
        )
        for row in rows[:8]
    )
    if speaks_burmese(staff_text):
        return f"ကြေညာချက်။ {shown}"
    return f"Announcements. {shown}"


def _named_plan(text: str) -> str | None:
    match = re.search(r"\bnamed\s+([A-Za-z0-9][A-Za-z0-9 ._-]{0,40})", text, re.IGNORECASE)
    if match is None:
        return None
    name = re.split(r"\b(?:if|and|do)\b", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = name.strip(" .?")
    return cleaned or None


def _ask_plans(rows: list[Mapping[str, object]], staff_text: str) -> str:
    shown = ", ".join(
        " ".join(
            part
            for part in (
                str(row.get("code", "")),
                str(row.get("name", "")),
                str(row.get("status", "")),
            )
            if part
        )
        for row in rows
    )
    if speaks_burmese(staff_text):
        return f"အစီအစဉ်အမည် တူတာ တစ်ခုမက ရှိပါတယ်။ ဘယ်ကုဒ်လဲ။ {shown}".strip()
    return f"More than one plan has that name. Which plan code? {shown}".strip()


def _mapping_rows(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    listed = result.get("results")
    if not isinstance(listed, list):
        return []
    return [row for row in listed if isinstance(row, Mapping)]


def _with_equivalent_dates(text: str) -> str:
    """Let a stored day support the same day written in another form."""

    from apps.assistant.lookups import _calendar_hits

    extra: list[str] = []
    for hit in _calendar_hits(text.translate(_MYANMAR_DIGITS)):
        if hit.kind != "date" or hit.day is None:
            continue
        day = hit.day
        extra.append(day.isoformat())
        extra.append(f"{day.day} {_MONTH_NAMES[day.month - 1]} {day.year}")
    if not extra:
        return text
    return text + "\n" + " ".join(extra)


def grounded_read_reply(
    reply: str,
    *,
    results: Iterable[Mapping[str, object]],
    brief: str,
    staff_text: str,
    fallback: bool = True,
) -> str:
    """Drop unsupported amounts, dates, codes, statuses, and names.

    A sentence that still contains a supported fact is kept. The stored-record
    rendering is used only when every sentence lost its facts.
    """

    stored = tuple(results)
    allowed_text = _with_equivalent_dates(
        "\n".join((staff_text, brief, json.dumps(stored, default=str, ensure_ascii=False)))
    )
    sentences: list[str] = []
    for sentence in _reply_sentences(reply):
        if _leaks(sentence):
            continue
        aligned = _align_financial_claims(sentence, stored)
        corrected = _correct_sentence(aligned, allowed_text)
        if corrected and not _leaks(corrected):
            sentences.append(corrected)
    body = " ".join(sentences).strip()
    if body:
        return _with_financial_omission(body, stored, staff_text)
    if not fallback:
        return ""
    return _with_financial_omission(_render_reads(stored, staff_text), stored, staff_text)


def _with_financial_omission(
    reply: str,
    results: tuple[Mapping[str, object], ...],
    staff_text: str,
) -> str:
    """Say when a dashboard result withheld financial amounts."""

    withheld = any("financial" in result and result.get("financial") is None for result in results)
    if not withheld:
        return reply
    note = (
        "ဒီစာရင်းရဲ့ ငွေကြေးပမာဏကို ကြည့်ခွင့် မရှိပါ။"
        if speaks_burmese(staff_text)
        else "You cannot view the financial amounts for that range."
    )
    if note in reply:
        return reply
    return f"{reply} {note}".strip()


def visible_reply(
    reply: str,
    *,
    confirmed: bool,
    proposed: bool,
    staff_text: str,
) -> str:
    """Remove leaked internals and unconfirmed success claims. Keep the rest."""

    burmese = speaks_burmese(staff_text) or speaks_burmese(reply)
    sentences = _reply_sentences(reply) or ([reply.strip()] if reply.strip() else [])
    kept: list[str] = []
    dropped_success = False
    dropped_leak = False
    for sentence in sentences:
        if _leaks(sentence):
            dropped_leak = True
            continue
        unconfirmed = (not confirmed) and (
            _SUCCESS.search(sentence) is not None or (proposed and _bare_done(sentence))
        )
        if unconfirmed:
            dropped_success = True
            continue
        kept.append(sentence)
    if kept:
        body = " ".join(kept).strip()
        if dropped_success:
            note = _unconfirmed_sentence(burmese)
            if note not in body:
                body = f"{body} {note}".strip()
        return body
    if dropped_leak:
        if burmese:
            return "မပြီးမြောက်ပါ။ ဘာမှ မပြောင်းရသေးပါ။"
        return "I could not complete that."
    if dropped_success:
        return _unconfirmed_sentence(burmese)
    return reply


def _unconfirmed_sentence(burmese: bool) -> str:
    if burmese:
        return "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ။ အတည်ပြုပြီးမှ သိမ်းပါမယ်။"
    return "That change is not saved until you confirm."


def _reply_sentences(text: str) -> list[str]:
    """Split on sentence endings. A decimal point stays inside the amount."""

    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        piece = text[start : match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


_CATEGORY_FIELDS = (
    "new_member_revenue",
    "renewal_revenue",
    "pt_revenue",
    "class_revenue",
    "product_revenue",
    "other_revenue",
    "total_revenue",
    "total_expenses",
    "net_profit",
)
_MONEY_CUES = (
    "revenue",
    "sale",
    "sales",
    "income",
    "profit",
    "expense",
    "ဝင်ငွေ",
    "အရောင်း",
    "အမြတ်",
    "အသုံး",
    "ကုန်ကျ",
    "အသင်းဝင်",
    "သက်တမ်းတိုး",
    "နည်းပြ",
)


def _align_financial_claims(sentence: str, results: tuple[Mapping[str, object], ...]) -> str:
    """Replace a category amount that contradicts that category's stored field.

    The sentence stays. Only the contradicted amount changes, and only when
    one dashboard snapshot identifies the category.
    """

    snapshot = _financial_snapshot(sentence, results)
    if snapshot is None:
        return sentence
    result, financial = snapshot
    translated = exact_code(sentence)
    matches = [
        match
        for match in _CLAIM_NUMBER.finditer(translated)
        if not _inside_date(translated, match.start())
    ]
    if not matches:
        return sentence
    labels = _label_spans(translated)
    edits: list[tuple[int, int, str]] = []
    for index, match in enumerate(matches):
        token = match.group(0)
        label = _nearest_label(labels, matches, index)
        if label is None:
            continue
        window = _amount_clause(translated, matches, index)
        if label == "class_revenue" and not _money_window(window):
            continue
        if _is_operational_count(token, result) and not _money_window(window):
            continue
        if not _money_window(window) and not _matches_stored_money(token, financial):
            continue
        expected = _amount_for_label(label, financial)
        if expected is None or _canon_number(token) == _canon_number(expected):
            continue
        edits.append((match.start(), match.end(), expected))
    updated = sentence
    for start, end, expected in reversed(edits):
        updated = updated[:start] + expected + updated[end:]
    return updated


def _financial_snapshot(
    sentence: str,
    results: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    snapshots: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for result in results:
        financial = result.get("financial")
        if isinstance(financial, Mapping) and any(key in financial for key in _CATEGORY_FIELDS):
            snapshots.append((result, financial))
    if len(snapshots) == 1:
        return snapshots[0]
    translated = exact_code(sentence)
    matched = [
        snapshot for snapshot in snapshots if _range_mentioned(snapshot[0].get("range"), translated)
    ]
    if len(matched) == 1:
        return matched[0]
    return None


def _range_mentioned(period: object, sentence: str) -> bool:
    if not isinstance(period, Mapping):
        return False
    start = period.get("date_from")
    end = period.get("date_to")
    return (isinstance(start, str) and start != "" and start in sentence) or (
        isinstance(end, str) and end != "" and end in sentence
    )


def _money_window(window: str) -> bool:
    folded = window.casefold()
    return any(word in folded or word in window for word in _MONEY_CUES)


def _matches_stored_money(token: str, financial: Mapping[str, object]) -> bool:
    canon = _canon_number(token)
    return any(
        isinstance(value, str) and _canon_number(value) == canon
        for value in (financial.get(field) for field in _CATEGORY_FIELDS)
    )


_LABEL_PATTERNS = (
    ("net_profit", re.compile(r"\bprofit\b|အမြတ်", re.IGNORECASE)),
    ("total_expenses", re.compile(r"\bexpenses?\b|အသုံး|ကုန်ကျ", re.IGNORECASE)),
    ("pt_revenue", re.compile(r"(?<![a-z0-9])pt(?![a-z0-9])|နည်းပြ", re.IGNORECASE)),
    (
        "class_revenue",
        re.compile(r"(?<![a-z0-9])class(?![a-z0-9])|အတန်း", re.IGNORECASE),
    ),
    ("product_revenue", re.compile(r"\bproducts?\b|\bretail\b|ကုန်ပစ္စည်း", re.IGNORECASE)),
    ("other_revenue", re.compile(r"\bother revenue\b|အခြား", re.IGNORECASE)),
    ("renewal_revenue", re.compile(r"\brenewals?\b|သက်တမ်းတိုး", re.IGNORECASE)),
    (
        "new_member_revenue",
        re.compile(r"\bnew[- ]members?\b|လူသစ်|အသင်းဝင်အသစ်", re.IGNORECASE),
    ),
    ("membership_revenue", re.compile(r"\bmemberships?\b|အသင်းဝင်", re.IGNORECASE)),
    ("total_revenue", re.compile(r"\btotal\b|စုစုပေါင်း|အားလုံး", re.IGNORECASE)),
    ("generic_revenue", re.compile(r"\brevenue\b|\bincome\b|ဝင်ငွေ", re.IGNORECASE)),
)
_GENERIC_LABELS = frozenset({"generic_revenue"})


def _label_spans(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for label, pattern in _LABEL_PATTERNS:
        for match in pattern.finditer(text):
            found.append((match.start(), match.end(), label))
    kept: list[tuple[int, int, str]] = []
    for start, end, label in found:
        covered = any(
            other_start <= start and other_end >= end and other_end - other_start > end - start
            for other_start, other_end, _other in found
        )
        if not covered:
            kept.append((start, end, label))
    return kept


def _nearest_label(
    labels: list[tuple[int, int, str]],
    amounts: list[re.Match[str]],
    index: int,
) -> str | None:
    if not labels:
        return None
    assigned: dict[int, list[str]] = {item: [] for item in range(len(amounts))}
    for start, end, label in labels:
        center = (start + end) / 2
        nearest = min(
            range(len(amounts)),
            key=lambda item: abs(amounts[item].start() - center),
        )
        assigned[nearest].append(label)
    unique = list(dict.fromkeys(assigned[index]))
    specific = [label for label in unique if label not in _GENERIC_LABELS]
    if len(specific) == 1:
        return specific[0]
    if len(unique) == 1:
        return unique[0]
    return None


def _amount_clause(text: str, amounts: list[re.Match[str]], index: int) -> str:
    start = amounts[index - 1].end() if index else 0
    end = amounts[index + 1].start() if index + 1 < len(amounts) else len(text)
    return text[start:end]


def _amount_for_label(label: str, financial: Mapping[str, object]) -> str | None:
    if label in {"membership_revenue", "generic_revenue"}:
        if label == "membership_revenue":
            return _sum_money(financial, "new_member_revenue", "renewal_revenue")
        value = financial.get("total_revenue")
        return value if isinstance(value, str) and value else None
    value = financial.get(label)
    return value if isinstance(value, str) and value else None


def _sum_money(financial: Mapping[str, object], *fields: str) -> str | None:
    total = Decimal(0)
    seen = False
    for field in fields:
        value = financial.get(field)
        if not isinstance(value, str) or value == "":
            continue
        try:
            total += Decimal(value)
        except InvalidOperation:
            return None
        seen = True
    if not seen:
        return None
    return format(total.quantize(Decimal("0.01")), "f")


def _is_operational_count(token: str, result: Mapping[str, object]) -> bool:
    if "." in token or "," in token:
        return False
    operational = result.get("operational")
    if not isinstance(operational, Mapping):
        return False
    canon = _canon_number(token)
    return any(str(value) == canon for value in operational.values() if isinstance(value, int))


def _correct_sentence(sentence: str, allowed_text: str) -> str:
    """Return the sentence, a trimmed sentence, or empty when its facts are gone."""

    if _sentence_supported(sentence, allowed_text):
        return sentence
    corrected = _drop_unsupported_claims(sentence, allowed_text)
    if not corrected or not _sentence_supported(corrected, allowed_text):
        return ""
    if _sentence_facts(sentence) and not _sentence_facts(corrected):
        return ""
    return corrected


def _sentence_supported(text: str, allowed_text: str) -> bool:
    if not text.strip():
        return False
    extra = _claim_tokens(text) - _claim_tokens(allowed_text)
    if extra and not _extra_are_derived(text, allowed_text, extra):
        return False
    if not _scaled_numbers_supported(text, allowed_text):
        return False
    return all(_name_supported(name, allowed_text) for name in _names(text))


def _sentence_facts(text: str) -> bool:
    return bool(_claim_tokens(text) or _names(text))


def _drop_unsupported_claims(text: str, allowed_text: str) -> str:
    allowed = _claim_tokens(allowed_text)
    translated = exact_code(text).replace("၊", "")
    spans: list[tuple[int, int]] = []
    for match in _CLAIM_DATE.finditer(translated):
        if match.group(0) not in allowed:
            spans.append(match.span())
    for match in _CLAIM_NUMBER.finditer(translated):
        percent = _percent_end(translated, match.end()) != match.end()
        if _number_supported(match.group(0), allowed_text, percent=percent):
            continue
        start = _leading_percent_start(translated, match.start())
        end = _unit_end(translated, match.end())
        spans.append((start, end))
    for pattern in (_CLAIM_CODE, _STATUS_RE):
        for match in pattern.finditer(translated):
            if match.group(0) not in allowed:
                spans.append(match.span())
    for name in _names(text):
        if _name_supported(name, allowed_text):
            continue
        start = text.find(name)
        if start >= 0:
            spans.append((start, start + len(name)))
    if not spans:
        return " ".join(text.split())
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return " ".join("".join(pieces).split())


def _bare_done(reply: str) -> bool:
    return reply.strip().casefold().removesuffix(".") in {"done", "ok", "okay"}


def _sentence(pair: tuple[str, str], staff_text: str) -> str:
    english, burmese = pair
    return burmese if speaks_burmese(staff_text) else english


def _with_exact(sentence: str, source: str) -> str:
    extra = [token for token in _exact_mentions(source) if token not in sentence]
    if not extra:
        return sentence
    return sentence + " " + " ".join(extra)


def _exact_mentions(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _EXACT.finditer(text):
        token = match.group(0)
        if token in _EXACT_SKIP:
            continue
        found.append(token)
    return tuple(dict.fromkeys(found))


_ASKS = re.compile(
    r"(\?|how many|how much|what about|what is|what's|which |show me|list |who |when did)",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<!\d)\.(?!\d)|[!?။]")
_ASK_PHRASES = (
    "ဘယ်လောက်",
    "ဘယ်နှစ်",
    "ဘယ်နှခု",
    "ဘယ်နှယောက်",
    "ဘယ်နှကြိမ်",
    "ပြပါ",
    "ပြပေး",
    "ရှာပေး",
    "ဘယ်သူ",
    "ဘယ်အချိန်",
    "သက်တမ်း",
    "နည်းနေ",
    "အများဆုံး",
)


_INTENT_MARKERS = ("လုပ်မယ်", "တက်မယ်", "ဝယ်မယ်", "ထည့်မယ်")


def _states_intent(text: str) -> bool:
    return any(marker in text for marker in _INTENT_MARKERS)


def _stored_lookup(text: str) -> bool:
    return (
        _mentions_granted_package(text)
        or _mentions_package_catalog(text)
        or _mentions_plan(text)
        or _mentions_expiry(text)
        or _mentions_checkin(text)
        or _mentions_pt_sale(text)
        or "ကျန်" in text
        or "ဘယ်လောက်" in text
        or "ဘယ်နှစ်" in text
        or "ဘယ်နှကြိမ်" in text
        or "ဘယ်နှယောက်" in text
    )


def _mentions_expiry(text: str) -> bool:
    folded = text.casefold()
    return "expir" in folded or "သက်တမ်း" in text


def _mentions_checkin(text: str) -> bool:
    folded = text.casefold()
    if "class" in folded or "အတန်း" in text:
        return False
    return (
        "check-in" in folded
        or "check in" in folded
        or "checkin" in folded
        or "ဝင်ရောက်" in text
        or "တက်ရောက်" in text
        or "လာရောက်" in text
        or "လာပြီး" in text
    )


def _mentions_assignment(text: str) -> bool:
    folded = text.casefold()
    return "assignment" in folded or "assigned" in folded or "workload" in folded or "တာဝန်" in text


def _mentions_trainer_list(text: str) -> bool:
    folded = text.casefold()
    return "trainer" in folded or "သင်တန်းဆရာ" in text or "နည်းပြ" in text


def _mentions_pt_sale(text: str) -> bool:
    folded = text.casefold()
    if "package" in folded or "ပက်ကေ့" in text:
        return False
    return ("pt" in folded or "personal training" in folded) and (
        "sale" in folded or "အရောင်း" in text
    )


def _mentions_pt_booking(text: str) -> bool:
    folded = text.casefold()
    return ("pt" in folded or "personal training" in folded) and (
        "booking" in folded or "booked" in folded or "ဘွတ်ကင်" in text
    )


def _mentions_class_type(text: str) -> bool:
    folded = text.casefold()
    return "class type" in folded or "အတန်းအမျိုးအစား" in text


def _mentions_schedule(text: str) -> bool:
    folded = text.casefold()
    return "schedule" in folded or "အချိန်ဇယား" in text or "recurring class" in folded


def _mentions_low_stock(text: str) -> bool:
    folded = text.casefold()
    return "low stock" in folded or "နည်းနေ" in text or "ကုန်နည်း" in text


def _mentions_stock_movement(text: str) -> bool:
    folded = text.casefold()
    return (
        "stock movement" in folded
        or "adjustment" in folded
        or "received" in folded
        or "ကုန်လှုပ်ရှား" in text
        or "လက်ခံ" in text
    )


def _mentions_product(text: str) -> bool:
    folded = text.casefold()
    return "product" in folded or "stock" in folded or "ကုန်ပစ္စည်း" in text


def _mentions_product_rank(text: str) -> bool:
    folded = text.casefold()
    return (
        "best selling" in folded
        or "top product" in folded
        or "အရောင်းအများဆုံး" in text
        or "အရောင်းအကောင်းဆုံး" in text
    )


def _mentions_staff_access(text: str) -> bool:
    folded = text.casefold()
    return ("permission" in folded or "ခွင့်ပြုချက်" in text) and ("staff" in folded or "ဝန်ထမ်း" in text)


def _mentions_role(text: str) -> bool:
    folded = text.casefold()
    return "role" in folded or "ရာထူး" in text


def _mentions_staff(text: str) -> bool:
    folded = text.casefold()
    return "staff" in folded or "ဝန်ထမ်း" in text


def _absent_question(text: str) -> bool:
    folded = text.casefold()
    return (
        "not checked" in folded
        or "no check-in" in folded
        or "haven't checked" in folded
        or "have not checked" in folded
        or "မလာသေး" in text
        or "မဝင်ရောက်" in text
    )


def _attended(text: str) -> bool:
    folded = text.casefold()
    return "attended" in folded or "who attended" in folded


def _upcoming(text: str) -> bool:
    folded = text.casefold()
    return "upcoming" in folded or "နောက်လာမယ့်" in text or "coming class" in folded


def _expiry_filters(
    payload: dict[str, object],
    *,
    staff_text: str,
    today: date | None,
    scope: str,
    window: tuple[date, date] | None,
    named_day: date | None,
) -> dict[str, object]:
    folded = staff_text.casefold()
    already = (
        "already expired" in folded
        or "expired membership" in folded
        or "ကုန်ပြီး" in staff_text
        or "ကုန်သွား" in staff_text
    )
    payload.pop("bucket", None)
    payload.pop("expiry_from", None)
    payload.pop("expiry_to", None)
    if window is not None and not already:
        payload["expiry_from"] = window[0].isoformat()
        payload["expiry_to"] = window[1].isoformat()
    elif scope == "today" and today is not None:
        payload["bucket"] = "today"
    elif already:
        payload["bucket"] = "expired"
    elif named_day is not None:
        payload["expiry_from"] = named_day.isoformat()
        payload["expiry_to"] = named_day.isoformat()
    return payload


def _service_date_filters(
    payload: dict[str, object],
    *,
    today: date | None,
    scope: str,
    window: tuple[date, date] | None,
    named_day: date | None,
    stated: str | None,
) -> dict[str, object]:
    payload.pop("service_date_from", None)
    payload.pop("service_date_to", None)
    if window is not None:
        payload["service_date_from"] = window[0].isoformat()
        payload["service_date_to"] = window[1].isoformat()
    elif scope == "today" and today is not None:
        payload["service_date_from"] = today.isoformat()
        payload["service_date_to"] = today.isoformat()
    elif named_day is not None:
        payload["service_date_from"] = named_day.isoformat()
        payload["service_date_to"] = named_day.isoformat()
    elif stated is not None:
        payload["service_date_from"] = stated
        payload["service_date_to"] = stated
    return payload


def _assignment_filters(payload: dict[str, object], staff_text: str) -> dict[str, object]:
    folded = staff_text.casefold()
    if "workload" in folded or "ဘယ်နှယောက်" in staff_text or "how many" in folded:
        payload["summary"] = True
    return payload


def _absence_filters(
    payload: dict[str, object],
    *,
    today: date | None,
    window: tuple[date, date] | None,
    named_day: date | None,
) -> dict[str, object]:
    payload.pop("date_from", None)
    payload.pop("date_to", None)
    if window is not None:
        payload["absent_since"] = window[0].isoformat()
    elif named_day is not None:
        payload["absent_since"] = named_day.isoformat()
    elif today is not None:
        payload["absent_since"] = (today - timedelta(days=30)).isoformat()
    return payload


def _mentions_plan(text: str) -> bool:
    folded = text.casefold()
    if "plan" in folded or "အစီအစဉ်" in text:
        return True
    return re.search(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b", text) is not None


def _mentions_package_catalog(text: str) -> bool:
    folded = text.casefold()
    if "package" not in folded and "ပက်ကေ့" not in text:
        return False
    if _mentions_granted_package(text):
        return False
    return True


def _mentions_granted_package(text: str) -> bool:
    folded = text.casefold()
    owned = "ဝယ်ထား" in text or "bought" in folded or "granted" in folded
    remaining = "remaining" in folded or "လက်ကျန်" in text or "ကျန်" in text
    named = "package" in folded or "ပက်ကေ့" in text or "session" in folded or "အကြိမ်" in text
    return named and (remaining or owned)


def _member_count_question(text: str) -> bool:
    folded = text.casefold()
    counted = "how many" in folded or "ဘယ်နှစ်" in text or "count" in folded or "အရေအတွက်" in text
    return counted and ("member" in folded or "အဖွဲ့ဝင်" in text)


def _mentions_class_schedule(text: str) -> bool:
    folded = text.casefold()
    if "package" in folded or "ပက်ကေ့" in text:
        return False
    return bool(re.search(r"\bclass", folded) or "အတန်း" in text)


def _asks(text: str) -> bool:
    if _ASKS.search(text):
        return True
    return any(phrase in text for phrase in _ASK_PHRASES)


def _relevant_brief_lines(brief: str, staff_text: str) -> list[str]:
    folded = staff_text.casefold()
    labels: list[str] = []
    if "plan" in folded or "အစီအစဉ်" in staff_text:
        labels.append("Membership plans:")
    if "package" in folded or "ပက်ကေ့" in staff_text or re.search(r"\bpt\b", folded):
        labels.append("Service packages:")
    if "trainer" in folded or "နည်းပြ" in staff_text:
        labels.append("Today's PT sessions:")
        labels.append("Active trainer assignments:")
    if not labels:
        return []
    return [line.strip() for line in brief.splitlines() if any(label in line for label in labels)]


def _is_change_request(text: str) -> bool:
    folded = text.casefold()
    if any(phrase in text or phrase in folded for phrase in _WRITE_PHRASES):
        return True
    return _WRITE_WORDS.search(text) is not None


def _record_gap(text: str) -> str | None:
    return _matched_domain(text, _GAP_PATTERNS)


def _lookup_domain(text: str) -> str | None:
    """Any lookup, including a count the brief can answer."""

    matched = _matched_domain(text, _GAP_PATTERNS)
    if matched is not None:
        return matched
    return _matched_domain(text, _LOOKUP_PATTERNS)


def _matched_domain(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    folded = text.casefold()
    for domain, phrases in patterns:
        if any(phrase in folded or phrase in text for phrase in phrases):
            return domain
    return None


def _date_problem_sentence(body: Mapping[str, object], burmese: bool, *, invalid: bool) -> str:
    token = body.get("token")
    token_text = token.strip() if isinstance(token, str) else ""
    first = body.get("first")
    second = body.get("second")
    stored = body.get("stored")
    kept = shown_day(stored) if isinstance(stored, str) and stored else ""
    if not invalid and isinstance(first, str) and isinstance(second, str) and first and second:
        earlier = shown_day(first)
        later = shown_day(second)
        if burmese:
            problem = f"{token_text} ကို {earlier} သို့မဟုတ် {later} လို့ ဖတ်လို့ရပါတယ်။"
        else:
            problem = f"{token_text} can be read as {earlier} or {later}."
    elif invalid:
        shown = token_text or "That date"
        if burmese:
            problem = f"{shown} က မှန်ကန်တဲ့ ရက်မဟုတ်ပါ။"
        else:
            problem = f"{shown} is not a real calendar day."
    else:
        shown = token_text or "That date"
        if burmese:
            problem = f"{shown} က နေ့ရက် မသေချာပါ။"
        else:
            problem = f"{shown} is not one clear calendar day."
    if kept:
        if burmese:
            return f"{problem} သိမ်းထားတဲ့ ရက်က {kept} ပါ။"
        return f"{problem} The stored date is still {kept}."
    if burmese:
        return f"{problem} ဘယ်ရက်လဲ။"
    return f"{problem} Which date?"


def _missing_question(fields: object, burmese: bool) -> str:
    names = fields if isinstance(fields, list) else []
    for name in names:
        if not isinstance(name, str):
            continue
        pair = _FIELD_QUESTIONS.get(name)
        if pair is not None:
            return pair[1] if burmese else pair[0]
    if burmese:
        return "လိုအပ်တဲ့ အချက်တစ်ခု ထပ်ပြောပေးပါ။"
    return "I need one missing detail before I can continue."


def _ambiguous_question(matches: object, burmese: bool) -> str:
    labels: list[str] = []
    rows = matches if isinstance(matches, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        when = row.get("date")
        if not isinstance(name, str) or name.strip() == "":
            continue
        label = name.strip()
        if isinstance(when, str) and when.strip():
            label = f"{label} {when.strip()}"
        labels.append(label)
    if not labels:
        return "ဘယ်ဟာလဲ။" if burmese else "Which one?"
    shown = ", ".join(labels)
    if burmese:
        return f"ဘယ်ဟာလဲ။ {shown}"
    return f"Which one? {shown}"


def _scaled_numbers_supported(text: str, allowed_text: str) -> bool:
    """A decimal amount is not proven by an unrelated small integer."""

    translated = exact_code(text)
    return all(
        _number_supported(
            match.group(0),
            allowed_text,
            percent=_percent_end(translated, match.end()) != match.end(),
        )
        for match in _CLAIM_NUMBER.finditer(translated)
    )


def _number_supported(token: str, allowed_text: str, *, percent: bool) -> bool:
    """A stored amount, a percent of two stored amounts, or an exact sum of two."""

    if _scaled_number_supported(token, allowed_text):
        return True
    if percent:
        return _derived_percent(token, allowed_text)
    return _exact_sum(token, allowed_text)


def _extra_are_derived(text: str, allowed_text: str, extra: set[str]) -> bool:
    derived: set[str] = set()
    translated = exact_code(text)
    for match in _CLAIM_NUMBER.finditer(translated):
        token = match.group(0)
        canon = _canon_number(token)
        if canon not in extra:
            continue
        percent = _percent_end(translated, match.end()) != match.end()
        if _number_supported(token, allowed_text, percent=percent):
            derived.add(canon)
    return extra <= derived


def _derived_percent(token: str, allowed_text: str) -> bool:
    amounts = _money_operands(allowed_text)
    for index, left in enumerate(amounts):
        for right in amounts[index + 1 :]:
            for numerator, denominator in ((left, right), (right, left)):
                if denominator == 0:
                    continue
                share = (numerator / denominator) * Decimal(100)
                change = ((numerator - denominator) / denominator) * Decimal(100)
                if _percent_matches(token, share) or _percent_matches(token, change):
                    return True
    return False


def _exact_sum(token: str, allowed_text: str) -> bool:
    try:
        claimed = Decimal(_canon_number(token))
    except InvalidOperation:
        return False
    amounts = list(dict.fromkeys(_money_operands(allowed_text)))
    for index, left in enumerate(amounts):
        for right in amounts[index + 1 :]:
            if left + right == claimed:
                return True
    return False


def _money_operands(allowed_text: str) -> list[Decimal]:
    text = exact_code(allowed_text)
    found: list[Decimal] = []
    for match in _CLAIM_NUMBER.finditer(text):
        if _inside_date(text, match.start()):
            continue
        raw = match.group(0)
        canon = _canon_number(raw)
        if "." not in raw and "," not in raw and len(canon) < 3:
            continue
        try:
            found.append(Decimal(canon))
        except InvalidOperation:
            continue
    return found


def _percent_matches(token: str, ratio: Decimal) -> bool:
    try:
        claimed = Decimal(_canon_number(token))
    except InvalidOperation:
        return False
    places = len(token.split(".", 1)[1]) if "." in token else 0
    quantum = Decimal(1).scaleb(-places)
    return ratio.quantize(quantum, rounding=ROUND_HALF_UP) == claimed


def _inside_date(text: str, index: int) -> bool:
    return any(match.start() <= index < match.end() for match in _CLAIM_DATE.finditer(text))


def _percent_end(text: str, end: int) -> int:
    match = re.match(r"\s*(?:%|percent\b|percentage\b)", text[end:], re.IGNORECASE)
    if match is None:
        return end
    return end + match.end()


def _unit_end(text: str, end: int) -> int:
    percent_end = _percent_end(text, end)
    if percent_end != end:
        return percent_end
    match = re.match(
        r"\s*(?:minutes?\b|mins?\b|hours?\b|hrs?\b|days?\b|weeks?\b|months?\b|years?\b|sessions?\b)",
        text[end:],
        re.IGNORECASE,
    )
    if match is None:
        return end
    return end + match.end()


def _leading_percent_start(text: str, start: int) -> int:
    match = re.search(r"%\s*$", text[:start])
    if match is None:
        return start
    return match.start()


def _scaled_number_supported(token: str, allowed_text: str) -> bool:
    canon = _canon_number(token)
    scaled = "." in token or "," in token
    for match in _CLAIM_NUMBER.finditer(exact_code(allowed_text)):
        source = match.group(0)
        if _canon_number(source) != canon:
            continue
        if not scaled or "." in source or "," in source or len(canon) >= 3:
            return True
    return False


def _canon_number(token: str) -> str:
    """Compare 1,500 and 1500.00 as the same stored amount."""

    try:
        number = Decimal(token.replace(",", ""))
    except InvalidOperation:
        return token.replace(",", "")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _claim_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        normalized = exact_code(part).replace("၊", "")
        flattened = normalized.replace(",", "")
        tokens.update(match.group(0) for match in _CLAIM_DATE.finditer(flattened))
        tokens.update(_canon_number(match.group(0)) for match in _CLAIM_NUMBER.finditer(normalized))
        tokens.update(match.group(0) for match in _CLAIM_CODE.finditer(normalized))
        tokens.update(match.group(0) for match in _STATUS_RE.finditer(normalized))
    return tokens


def _names(text: str) -> set[str]:
    return {match.group(0) for match in _CLAIM_NAME.finditer(exact_code(text))}


def _name_supported(name: str, allowed_text: str) -> bool:
    if _has_term(name, allowed_text):
        return True
    return all(_has_term(word, allowed_text) for word in name.split())


def _has_term(term: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _render_reads(results: tuple[Mapping[str, object], ...], staff_text: str) -> str:
    rows = _read_rows(results)
    phrases = [" ".join(_row_bits(row)) for row in rows[:8]]
    phrases = [phrase for phrase in phrases if phrase]
    if not phrases:
        if speaks_burmese(staff_text):
            return "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။"
        return "No stored record matches that."
    body = "; ".join(phrases)
    if speaks_burmese(staff_text):
        return f"သိမ်းထားတဲ့ မှတ်တမ်း။ {body}"[:1500]
    return f"Stored records. {body}"[:1500]


def _read_rows(results: tuple[Mapping[str, object], ...]) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for result in results:
        listed = result.get("results")
        if isinstance(listed, list):
            rows.extend(item for item in listed if isinstance(item, Mapping))
            continue
        rows.append(result)
    return rows


def _row_bits(row: Mapping[str, object]) -> list[str]:
    bits: list[str] = []
    _append_bits(row, bits, depth=0)
    return bits


def _append_bits(row: Mapping[str, object], bits: list[str], *, depth: int) -> None:
    for key in sorted(row):
        if key in _ROW_SKIP or key.endswith("_id"):
            continue
        value = row[key]
        if isinstance(value, Mapping) and depth < 2:
            _append_bits(value, bits, depth=depth + 1)
            continue
        if isinstance(value, bool) or value is None or isinstance(value, list | dict):
            continue
        if not isinstance(value, str | int | float):
            continue
        text = str(value).strip()
        folded = text.casefold()
        if text and len(text) <= 80 and "traceback" not in folded and "select *" not in folded:
            bits.append(text)


def _leaks(reply: str) -> bool:
    folded = reply.casefold()
    if any(token in folded for token in _LEAK_TEXT):
        return True
    if "_" not in reply:
        return False
    from apps.assistant.registry import production_registry

    return any(name in reply for name in production_registry().names())

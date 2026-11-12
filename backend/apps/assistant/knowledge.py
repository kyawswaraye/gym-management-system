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
from decimal import Decimal
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
a member, a membership, a granted package, a payment, a class occurrence, a
class booking, a retail sale, an expense, a salary period, a cash session,
a PT commission, an announcement, and a dashboard total. A refund is a payment
whose entry type is REFUND. A new membership sale uses an active plan. Use
the tool result for those records. Check-in history, stock movements,
trainer-assignment lists, PT sale records, and class-schedule definitions
are not reads. The brief may show a count or today's list. If the
requested record is not in the brief, say it cannot be retrieved. Changing
those records is a confirmed write, and a write is not a substitute for a
read. Mail, files, push notifications, passwords, sessions, audit storage,
and a daily shift roster are not supported. Say that plainly. Do not invent
the record. If a brief section is absent, this staff member cannot see it.
Say so in one sentence and do not reveal the hidden records. Do not ask what
else to help with. Never send the staff to a screen.

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

The assistant does not send mail, push notifications, or files, and it does not
read passwords, MFA, sessions, or audit storage. It does not invent a record
when that read is not registered. A write is not saved until the server confirms it.
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
        "Live gym brief. These are stored records, not examples.",
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
                    .values_list("name", "type", "session_count")[:_NAME_LIMIT]
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
            f"A class no-show {consume} a session."
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
    "memberships": frozenset({"search_memberships"}),
    "packages": frozenset({"search_pt_packages"}),
    "classes": frozenset({"search_class_occurrences"}),
    "bookings": frozenset({"search_bookings"}),
    "sales": frozenset({"search_sales"}),
    "expenses": frozenset({"search_expenses"}),
    "salary": frozenset({"search_salary_periods"}),
    "cash": frozenset({"search_cash_sessions"}),
    "commissions": frozenset({"search_pt_commissions"}),
    "payments": frozenset({"search_payments", "get_payment"}),
    "announcements": frozenset({"search_announcements"}),
    "reports": frozenset({"get_gym_dashboard"}),
}
_WRITE_ONLY_DOMAINS = frozenset(
    {
        "checkins",
        "assignments",
        "pt_sales",
        "inventory",
        "schedules",
        "staff",
        "roles",
        "settings",
    }
)
_UNSUPPORTED_DOMAINS = frozenset({"audit", "mail", "files", "passwords", "notifications", "roster"})
_GAP_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("roster", ("shift roster", "daily shift", "အဆိုင်း")),
    ("audit", ("audit log", "audit trail", "စစ်ဆေးမှတ်တမ်း")),
    ("passwords", ("password", "စကားဝှက်")),
    ("mail", ("send mail", "send email", "အီးမေးလ်ပို့", "မေးလ်ပို့")),
    ("files", ("upload a file", "upload file", "ဖိုင်တင်")),
    ("notifications", ("push notification", "အကြောင်းကြားစာ")),
    (
        "checkins",
        (
            "check-in history",
            "check in history",
            "attendance history",
            "check-in record",
            "check-in for",
            "check-ins for",
            "ဝင်ရောက်မှတ်တမ်း",
            "တက်ရောက်မှတ်တမ်း",
            "လာရောက်မှတ်တမ်း",
        ),
    ),
    ("inventory", ("stock movement", "stock history", "adjustment history", "ကုန်လှုပ်ရှား")),
    ("assignments", ("who is assigned", "assignment list", "assignment history", "တာဝန်ပေးမှု")),
    ("pt_sales", ("pt sale", "pt sales", "pt အရောင်း")),
    ("schedules", ("class schedule", "weekly schedule", "အပတ်စဉ်အချိန်ဇယား")),
    ("roles", ("role list", "permission list", "ခွင့်ပြုချက်စာရင်း")),
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
    ("members", ("member", "အဖွဲ့ဝင်")),
)
_LIMITS = {
    "checkins": (
        "I cannot look up check-in history. I only have today's check-in count, "
        "and recording a check-in waits for your confirmation.",
        "ဝင်ရောက်မှတ်တမ်းကို ကျွန်တော် မဖတ်နိုင်ပါ။ ဒီနေ့ စုစုပေါင်းကိုသာ သိနိုင်ပြီး၊ မှတ်တမ်းတင်ရန် အတည်ပြုချက် လိုပါတယ်။",
    ),
    "inventory": (
        "I cannot read stock movements. I can see on-hand quantity, and a stock "
        "change waits for your confirmation.",
        "ကုန်လှုပ်ရှားမှုမှတ်တမ်းကို ကျွန်တော် မဖတ်နိုင်ပါ။ လက်ကျန်ကိုသာ သိနိုင်ပြီး၊ ကုန်ပြောင်းရန် အတည်ပြုချက် လိုပါတယ်။",
    ),
    "assignments": (
        "I cannot read the assignment list. I only have the active count, and "
        "creating an assignment waits for your confirmation.",
        "တာဝန်ပေးမှုစာရင်းကို ကျွန်တော် မဖတ်နိုင်ပါ။ အရေအတွက်ကိုသာ သိနိုင်ပြီး၊ တာဝန်ပေးရန် အတည်ပြုချက် လိုပါတယ်။",
    ),
    "pt_sales": (
        "I cannot read a PT sale record. I only have counts by status, and "
        "creating one waits for your confirmation.",
        "PT အရောင်းမှတ်တမ်းကို ကျွန်တော် မဖတ်နိုင်ပါ။ အခြေအနေအလိုက် အရေအတွက်ကိုသာ သိနိုင်ပြီး၊ အရောင်းတင်ရန် အတည်ပြုချက် လိုပါတယ်။",
    ),
    "schedules": (
        "I cannot read a class-schedule definition. I can see today's classes.",
        "အတန်းအချိန်ဇယား အသေးစိတ်ကို ကျွန်တော် မဖတ်နိုင်ပါ။ ဒီနေ့ အတန်းကိုသာ သိနိုင်ပါတယ်။",
    ),
    "roles": (
        "I cannot read the role or permission list. Changing a role waits for your confirmation.",
        "ခွင့်ပြုချက်စာရင်းကို ကျွန်တော် မဖတ်နိုင်ပါ။ ပြောင်းရန် အတည်ပြုချက် လိုပါတယ်။",
    ),
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
_BRIEF = re.compile(r"\b(how many|how much|count|total|today)\b", re.IGNORECASE)
_HISTORY = ("history", "record", "records", "မှတ်တမ်း")
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
    r"သိမ်းပြီး|ပြီးပါပြီ|အောင်မြင်ပါပြီ|မှတ်ပြီးပါပြီ)",
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
_CLAIM_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
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
        "time_unconfirmed",
        "missing_fields",
        "ambiguous",
        "not_found",
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


def refuse_write_for_lookup(text: str, operation: str) -> bool:
    """A lookup must not be answered by proposing a write."""

    if operation != "write" or _is_change_request(text):
        return False
    if short_reply_kind(text):
        return False
    return _lookup_domain(text) is not None


_AFFIRMATIONS = frozenset({"ထည့်မယ်", "အင်း", "ဟုတ်တယ်", "ရတယ်", "လုပ်ပါ"})
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
    if token in _DECLINES:
        return "decline"
    if token in _AFFIRMATIONS:
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
    if error == "date_unconfirmed":
        return "ဘယ်ရက်လဲ။" if burmese else "Which date?"
    if error == "time_unconfirmed":
        return "အချိန် ဘယ်လောက်လဲ။" if burmese else "What time?"
    if error == "missing_fields":
        return _missing_question(body.get("fields"), burmese)
    if error == "ambiguous":
        return _ambiguous_question(body.get("matches"), burmese)
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
_CLASS_DATE_TOOLS = frozenset({"search_class_occurrences", "search_bookings"})
_BUSINESS_DATE_TOOLS = frozenset(
    {
        "search_sales",
        "search_expenses",
        "search_salary_periods",
        "search_cash_sessions",
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

    if tool_name not in listing_tools(staff_text):
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
    revenue = financial.get("total_revenue")
    expenses = financial.get("total_expenses")
    if speaks_burmese(staff_text):
        return f"{start} မှ {end}။ ဝင်ငွေ {revenue}။ အသုံးစရိတ် {expenses}။"
    return f"{start} to {end}. Revenue {revenue}. Expenses {expenses}."


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


def grounded_read_reply(
    reply: str,
    *,
    results: Iterable[Mapping[str, object]],
    brief: str,
    staff_text: str,
) -> str:
    """Keep a read answer only when its facts are in the result or the brief."""

    stored = tuple(results)
    allowed_text = "\n".join(
        (staff_text, brief, json.dumps(stored, default=str, ensure_ascii=False))
    )
    claims_ok = _claim_tokens(reply) <= _claim_tokens(allowed_text)
    names_ok = all(_name_supported(name, allowed_text) for name in _names(reply))
    if reply.strip() and claims_ok and names_ok:
        return reply
    return _render_reads(stored, staff_text)


def visible_reply(
    reply: str,
    *,
    confirmed: bool,
    proposed: bool,
    staff_text: str,
) -> str:
    """Drop leaks and success claims the server did not confirm."""

    if _leaks(reply):
        if speaks_burmese(staff_text) or speaks_burmese(reply):
            return "မပြီးမြောက်ပါ။ ဘာမှ မပြောင်းရသေးပါ။"
        return "I could not complete that."
    if confirmed:
        return reply
    false_success = _SUCCESS.search(reply) is not None or (proposed and _bare_done(reply))
    if false_success:
        if speaks_burmese(staff_text) or speaks_burmese(reply):
            return "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ။ အတည်ပြုပြီးမှ သိမ်းပါမယ်။"
        return "That change is not saved until you confirm."
    return reply


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


def _is_change_request(text: str) -> bool:
    folded = text.casefold()
    if any(phrase in text or phrase in folded for phrase in _WRITE_PHRASES):
        return True
    return _WRITE_WORDS.search(text) is not None


def _record_gap(text: str) -> str | None:
    if _checkin_record(text):
        return "checkins"
    domain = _matched_domain(text, _GAP_PATTERNS)
    if domain is None:
        return None
    if domain in {"checkins", "announcements", "assignments", "pt_sales", "schedules"}:
        if _brief_question(text):
            return None
    if domain == "inventory" and _stock_on_hand(text):
        return None
    return domain


def _checkin_record(text: str) -> bool:
    folded = text.casefold()
    if "class attendance" in folded or "who attended" in folded:
        return False
    mentioned = "check-in" in folded or "check in" in folded or "ဝင်ရောက်" in text or "တက်ရောက်" in text
    return mentioned and not _brief_question(text)


def _brief_question(text: str) -> bool:
    folded = text.casefold()
    if any(token in folded or token in text for token in _HISTORY):
        return False
    if _BRIEF.search(text):
        return True
    return "ဒီနေ့" in text or "ဘယ်နှစ်" in text or "အရေအတွက်" in text


def _stock_on_hand(text: str) -> bool:
    folded = text.casefold()
    return "on hand" in folded or "လက်ကျန်" in text or bool(_BRIEF.search(text))


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


def _claim_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        normalized = exact_code(part)
        flattened = normalized.replace(",", "").replace("၊", "")
        tokens.update(match.group(0) for match in _CLAIM_DATE.finditer(flattened))
        tokens.update(match.group(0) for match in _CLAIM_NUMBER.finditer(flattened))
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
    for key in sorted(row):
        if key in _ROW_SKIP or key.endswith("_id"):
            continue
        value = row[key]
        if isinstance(value, bool) or value is None or isinstance(value, list | dict):
            continue
        if not isinstance(value, str | int | float):
            continue
        text = str(value).strip()
        folded = text.casefold()
        if text and len(text) <= 80 and "traceback" not in folded and "select *" not in folded:
            bits.append(text)
    return bits


def _leaks(reply: str) -> bool:
    folded = reply.casefold()
    if any(token in folded for token in _LEAK_TEXT):
        return True
    if "_" not in reply:
        return False
    from apps.assistant.registry import production_registry

    return any(name in reply for name in production_registry().names())

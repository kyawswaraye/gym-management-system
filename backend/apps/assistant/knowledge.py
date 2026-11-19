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
member are already chosen. Every business row belongs to that gym. Do not
invent a person, amount, date, or count that is not in the gym brief or a
tool result. If it is not there, it cannot be retrieved. If a brief section
is absent, this staff member cannot see it. Say that plainly. Never send the
staff to a screen. A write is not a substitute for a read, and a write is not
saved until the server confirms it.

Records. A member is the customer: number, name, phone, and status ACTIVE,
EXPIRED, SUSPENDED, CANCELLED, or ARCHIVED. A membership plan is gym access
for a duration and price. A membership is that member's purchased term, with
its own dates, status, and payment status. Expiry is the end date, not the
member status. A renewal is a new membership. Nothing extends those dates.
Suspending a membership freezes that term. A class is a scheduled group
meeting and is not a membership. A class package is a block of class sessions.
A PT package is personal training with one trainer. The platform subscription
is what this gym pays for GymPilot and is not a member membership.

Money. Payments, PT sales, expenses, retail sales, and salary periods share
one life: DRAFT, then PENDING_APPROVAL when finance approval is separate,
then POSTED. A draft is not posted until it is submitted and approved. The
creator cannot approve their own record. A draft can be CANCELLED. A waiting
document is REJECTED and needs a reason. A posted document can be VOID. A
payment can also be REFUNDED. A CASH payment, sale, or expense needs an OPEN
cash session before approval. The till id is not chosen in the request.
Unpaid and partial memberships are the member debt. pending_payment_amount
is that outstanding membership money. Revenue is split into new-member,
renewal, PT, class, product, and other. Quote total_revenue and net_profit
exactly from the tool result. Never add, subtract, or recompute money fields.

Ids and language. Use a verified reference id. Never ask for or send a gym
id. Keep stored names, numbers, codes, dates, amounts, and statuses exactly
as stored. Myanmar digits have the same values as Arabic digits. Use the gym
timezone for relative days. There is no daily shift roster. Passwords,
sessions, audit storage, a one-off email, and a file upload are not
supported. Publishing an announcement queues its notices and push. Membership
expiry email and push run only when gym settings enable them.
""".strip()


def assistant_context(
    gym: Gym,
    codes: frozenset[str],
    full_access: bool,
    sections: frozenset[str] | None = None,
) -> str:
    """Return the domain map plus this gym's permitted live records."""

    try:
        facts = gym_facts(gym, codes, full_access, sections)
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


def gym_facts(
    gym: Gym,
    codes: frozenset[str],
    full_access: bool,
    sections: frozenset[str] | None = None,
) -> str:
    """Summarize the current gym. Hidden areas are omitted, not guessed."""

    visible = included_sections(codes, full_access)
    if sections is not None:
        visible &= sections
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
    "join_date": ("Which join date?", "ဘယ်ရက်လဲ။"),
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
    "rejected": (
        "That change was not saved. Say the step again with the record it applies to.",
        "ဒီပြောင်းလဲမှုကို မသိမ်းပါ။ မှတ်တမ်းနဲ့ လုပ်ချင်တဲ့ အဆင့်ကို ပြန်ပြောပါ။",
    ),
    "invalid": (
        "The stored change was not accepted. Say the step again with the values you want.",
        "သိမ်းထားတဲ့ ပြောင်းလဲမှုကို လက်မခံပါ။ လိုချင်တဲ့ အချက်တွေကို ပြန်ပြောပါ။",
    ),
    "assignment": (
        "The trainer must be assigned to the member on that date before this change. "
        "Assign the trainer first.",
        "ဒီရက်မှာ သင်တန်းဆရာကို အဖွဲ့ဝင်နဲ့ တာဝန်မပေးရသေးပါ။ အရင် တာဝန်ပေးပါ။",
    ),
    "assignment_required": (
        "That trainer is not assigned to this member on that date. Assign the trainer "
        "before the sale.",
        "ဒီရက်မှာ သင်တန်းဆရာကို အဖွဲ့ဝင်နဲ့ တာဝန်မပေးရသေးပါ။ အရောင်းမတိုင်ခင် တာဝန်ပေးပါ။",
    ),
    "package": (
        "That booking needs an active PT package with sessions left. A posted PT sale comes first.",
        "ဒီဘိုကင်အတွက် လက်ကျန်ရှိတဲ့ PT ပက်ကေ့ လိုပါတယ်။ PT အရောင်းကို အရင်တင်ပါ။",
    ),
    "package_required": (
        "That booking needs an active PT package with sessions left. A posted PT sale comes first.",
        "ဒီဘိုကင်အတွက် လက်ကျန်ရှိတဲ့ PT ပက်ကေ့ လိုပါတယ်။ PT အရောင်းကို အရင်တင်ပါ။",
    ),
    "class_package": (
        "That class needs an active class package with sessions left. Sell a class package first.",
        "ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။ အတန်းပက်ကေ့ကို အရင် ရောင်းပါ။",
    ),
    "class_package_required": (
        "That class needs an active class package with sessions left. Sell a class package first.",
        "ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။ အတန်းပက်ကေ့ကို အရင် ရောင်းပါ။",
    ),
    "member_inactive": (
        "That member is not active. Choose an active member.",
        "ဒီအဖွဲ့ဝင်က အသုံးမပြုနိုင်ပါ။ အသုံးပြုနိုင်တဲ့ အဖွဲ့ဝင်ကို ပြောပါ။",
    ),
    "plan_inactive": (
        "That plan is not active. Choose an active plan.",
        "ဒီအစီအစဉ်က အသုံးမပြုနိုင်ပါ။ အသုံးပြုနိုင်တဲ့ အစီအစဉ်ကို ပြောပါ။",
    ),
    "membership_overlap": (
        "Those dates overlap another membership. Choose dates that do not overlap.",
        "ဒီရက်တွေက အခြားအသင်းဝင်ကြေးနဲ့ ထပ်နေပါတယ်။ မထပ်တဲ့ ရက်ကို ပြောပါ။",
    ),
    "membership_required": ("Which membership?", "ဘယ်အသင်းဝင်ကြေးလဲ။"),
    "membership_mismatch": (
        "That membership does not belong to this member. Choose one of this member's memberships.",
        "ဒီအသင်းဝင်ကြေးက ဒီအဖွဲ့ဝင်ရဲ့ မဟုတ်ပါ။ ဒီအဖွဲ့ဝင်ရဲ့ အသင်းဝင်ကြေးကို ပြောပါ။",
    ),
    "amount_unconfirmed": ("What amount?", "ပမာဏ ဘယ်လောက်လဲ။"),
    "amount_mismatch": (
        "That amount is not the amount you said. Repeat the amount you want.",
        "ပြောထားတဲ့ ပမာဏနဲ့ မတူပါ။ လိုချင်တဲ့ ပမာဏကို ပြန်ပြောပါ။",
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
        "That amount is more than the outstanding balance. Use an amount within the balance.",
        "ပမာဏက ပေးရန်ကျန်ငွေထက် များနေပါတယ်။ ပေးရန်ကျန်ငွေထက် မပိုတဲ့ ပမာဏကို ပြောပါ။",
    ),
    "class_full": (
        "That class has no remaining places. Choose another class.",
        "ဒီအတန်းမှာ နေရာမကျန်ပါ။ အခြားအတန်းကို ရွေးပါ။",
    ),
    "duplicate_booking": (
        "That member already has a booking for this class. Choose another class.",
        "ဒီအဖွဲ့ဝင်က ဒီအတန်းမှာ ဘိုကင်ရှိပြီးပါပြီ။ အခြားအတန်းကို ရွေးပါ။",
    ),
    "schedule_conflict": (
        "That time is already booked. Choose a different time.",
        "ဒီအချိန်မှာ ဘိုကင်ရှိပြီးပါပြီ။ အခြားအချိန်ကို ပြောပါ။",
    ),
    "trainer_unavailable": (
        "That trainer is already booked at that time. Choose a different time or trainer.",
        "ဒီသင်တန်းဆရာက ဒီအချိန်မှာ ဘိုကင်ရှိပြီးပါပြီ။ အခြားအချိန် သို့မဟုတ် သင်တန်းဆရာကို ပြောပါ။",
    ),
    "time_order": (
        "The end time has to be after the start time.",
        "အဆုံးအချိန်က စတင်အချိန်ထက် နောက်ကျရပါမယ်။",
    ),
    "stock_short": (
        "There is not enough stock for that quantity. Ask for a quantity that is in stock.",
        "တောင်းတဲ့ အရေအတွက်အတွက် လက်ကျန်မလုံလောက်ပါ။ လက်ကျန်ထက် မပိုတဲ့ အရေအတွက်ကို ပြောပါ။",
    ),
    "product_inactive": (
        "That product is not active. Choose an active product.",
        "ဒီပစ္စည်းက အသုံးမပြုနိုင်ပါ။ အသုံးပြုနိုင်တဲ့ ပစ္စည်းကို ပြောပါ။",
    ),
    "quantity_invalid": (
        "Tell me a positive whole-number quantity.",
        "အရေအတွက်ကို ကိန်းပြည့် အပေါင်းနဲ့ ပြောပေးပါ။",
    ),
    "quantity_unconfirmed": ("What quantity?", "အရေအတွက် ဘယ်လောက်လဲ။"),
    "duplicate_product": (
        "Each product can be on the sale once. Keep each product on one line.",
        "ပစ္စည်းတစ်ခုကို တစ်ကြောင်းပဲ ထည့်ပါ။",
    ),
    "payment_unconfirmed": ("Which payment?", "ဘယ်ငွေပေးချေမှုလဲ။"),
    "payment_state": (
        "That payment cannot be changed in its current state. Say the next step that matches it.",
        "ဒီငွေပေးချေမှုကို အခုအခြေအနေမှာ ပြင်လို့ မရပါ။ အခြေအနေနဲ့ ကိုက်တဲ့ နောက်တစ်ဆင့်ကို ပြောပါ။",
    ),
    "retail_charge": (
        "Use the retail return for that product sale.",
        "ဒီပစ္စည်းအရောင်းကို ပြန်အမ်းလမ်းကြောင်းကနေ လုပ်ပါ။",
    ),
    "refund_exceeds": (
        "That refund is more than the refundable balance. Use an amount within that balance.",
        "ပြန်အမ်းငွေက ပြန်အမ်းနိုင်တဲ့ ပမာဏထက် များနေပါတယ်။ အဲဒီပမာဏထက် မပိုအောင် ပြောပါ။",
    ),
    "void_blocked": (
        "That payment cannot be fully reversed. Refund the remaining balance instead.",
        "ဒီငွေပေးချေမှုကို အပြည့်အဝ ပြန်လှန်လို့ မရပါ။ ကျန်ငွေကို ပြန်အမ်းပါ။",
    ),
    "date_invalid": (
        "That date cannot be used. Say a real date.",
        "ဒီရက်ကို အသုံးမပြုနိုင်ပါ။ ရှိတဲ့ ရက်ကို ပြောပါ။",
    ),
    "unmetered_denied": (
        "An unmetered booking is not allowed for this account. Use a package with sessions left.",
        "အထုပ်မဲ့ ဘိုကင်လုပ်ခွင့် မရှိပါ။ လက်ကျန်ရှိတဲ့ ပက်ကေ့ကို သုံးပါ။",
    ),
    "unmetered_reason": (
        "Tell me the reason for the unmetered booking.",
        "အထုပ်မဲ့ ဘိုကင်အတွက် အကြောင်းပြချက် ပြောပေးပါ။",
    ),
    "missing_record": (
        "That record was not found in this gym. Say the name or number again.",
        "ဒီအားကစားခန်းမမှာ မှတ်တမ်း မတွေ့ပါ။ နာမည် သို့မဟုတ် နံပါတ်ကို ပြန်ပြောပါ။",
    ),
    "check_in_ineligible": (
        "This member does not have an eligible active membership. "
        "Use a member with an active paid membership, or record that payment first.",
        "ဒီအဖွဲ့ဝင်မှာ ဝင်ရောက်နိုင်တဲ့ အသင်းဝင်ကြေး မရှိပါ။ "
        "ပေးချေပြီးသား အသင်းဝင်ကြေးရှိတဲ့ အဖွဲ့ဝင်ကို သုံးပါ၊ သို့မဟုတ် ငွေပေးချေမှုကို အရင် မှတ်ပါ။",
    ),
    "check_in_open": (
        "This member is already checked in. Check out before checking in again.",
        "ဒီအဖွဲ့ဝင် ဝင်ထားပြီးသားပါ။ ပြန်ထွက်ပြီးမှ ပြန်ဝင်ပါ။",
    ),
    "check_in_duplicate": (
        "This member was already checked in. No further check-in is needed for this period.",
        "ဒီအဖွဲ့ဝင်ကို ဒီကာလမှာ ဝင်ထားပြီးသားပါ။ ထပ်ဝင်စရာ မလိုပါ။",
    ),
    "already_saved": (
        "That change is already stored. Nothing else needs to be saved for it.",
        "ဒီပြောင်းလဲမှု ရှိပြီးသားပါ။ ထပ်သိမ်းစရာ မလိုပါ။",
    ),
    "sale_state": (
        "That sale is not posted yet. Approve it before returning goods.",
        "အရောင်းကို မတင်ရသေးပါ။ ပြန်အမ်းဖို့ အရင် အတည်ပြုပါ။",
    ),
    "finance_state": (
        "This record cannot take this step yet. Say whether to submit it, approve it, or leave it.",
        "ဒီမှတ်တမ်းက ဒီအဆင့်ကို မရသေးပါ။ တင်သွင်းမလား၊ အတည်ပြုမလား၊ ဒီအတိုင်းထားမလား ပြောပါ။",
    ),
    "not_pending": (
        "This record is not waiting for approval. Submit a draft, or leave a posted record.",
        "ဒီမှတ်တမ်းက အတည်ပြုဖို့ စောင့်မနေပါ။ မူကြမ်းဆိုရင် တင်သွင်းပါ။ တင်ပြီးသားဆိုရင် ဒီအတိုင်းထားပါ။",
    ),
    "cash_session_required": (
        "A cash payment needs an open till. Open the till, then continue this step.",
        "ငွေသားပေးချေမှုအတွက် ငွေသားစာရင်း ဖွင့်ထားဖို့ လိုပါတယ်။ စာရင်းဖွင့်ပြီး ဒီအဆင့်ကို ဆက်လုပ်ပါ။",
    ),
}
_STATUS_NEXT = {
    "DRAFT": (
        "This record is still a draft. Submit it before it can be approved.",
        "ဒီမှတ်တမ်းက မူကြမ်းဖြစ်နေပါသေးတယ်။ အတည်မပြုခင် အရင် တင်သွင်းပါ။",
    ),
    "PENDING_APPROVAL": (
        "This record is already submitted and waiting for approval. Confirm that approval.",
        "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ ဆက်လုပ်ဖို့ အတည်ပြုချက်ကို အတည်ပြုပါ။",
    ),
    "POSTED": (
        "This record is already posted. Nothing else needs to be saved.",
        "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ ထပ်သိမ်းစရာ မလိုပါ။",
    ),
    "REJECTED": (
        "This record was rejected. Create a new draft if it should be recorded again.",
        "ဒီမှတ်တမ်းကို ငြင်းပြီးပါပြီ။ ပြန်မှတ်ချင်ရင် မူကြမ်းအသစ် ပြောပါ။",
    ),
    "CANCELLED": (
        "This record is cancelled. Create a new draft if it should be recorded again.",
        "ဒီမှတ်တမ်းကို ပယ်ဖျက်ပြီးပါပြီ။ ပြန်မှတ်ချင်ရင် မူကြမ်းအသစ် ပြောပါ။",
    ),
    "VOID": (
        "This record is already void. Nothing else needs to be saved.",
        "ဒီမှတ်တမ်းကို ပြန်လှန်ပြီးပါပြီ။ ထပ်သိမ်းစရာ မလိုပါ။",
    ),
}
_PENDING_OTHER = (
    "This record is already submitted and waiting for approval. "
    "Another staff member who can approve must confirm it.",
    "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ အတည်ပြုခွင့်ရှိတဲ့ အခြားဝန်ထမ်းက အတည်ပြုရပါမယ်။",
)
_RECOVERY_FALLBACK = (
    "This step cannot be done yet. Repeat the fact you want changed.",
    "ဒီအဆင့်ကို မလုပ်နိုင်သေးပါ။ ပြောထားတဲ့ အချက်ကို ပြန်ပြောပေးပါ။",
)
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
_MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")
_SUCCESS = re.compile(
    r"(\bis saved\b|\bhas been saved\b|\bwas saved\b|\bsuccessfully\b|"
    r"\bi saved\b|\bi've saved\b|\bi have saved\b|"
    r"\balready with finance\b|"
    r"\bregistered\b|\bcreated\b|\bcompleted\b|"
    r"သိမ်းပြီး|ပြီးပါပြီ|အောင်မြင်ပါပြီ|အောင်မြင်စွာ|မှတ်ပြီးပါပြီ|တင်ပြထားပြီး|အပြီးသတ်)",
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


def exact_code(text: str) -> str:
    """Translate Myanmar digits in a code without adding or dropping any."""

    return text.translate(_MYANMAR_DIGITS)


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


def grounded_information_reply(reply: str, *, brief: str, staff_text: str) -> str:
    """Drop unsupported details. Return empty when every sentence is unsupported."""

    return grounded_read_reply(
        reply,
        results=(),
        brief=brief,
        staff_text=staff_text,
        fallback=False,
    )


_AFFIRMATIONS = frozenset({"ထည့်မယ်", "အင်း", "ဟုတ်တယ်", "ရတယ်", "လုပ်ပါ", "yes", "yeah", "ok", "okay"})
_DECLINES = frozenset({"မထည့်တော့ဘူး", "မလုပ်တော့ဘူး", "မဟုတ်ဘူး"})


def short_reply_kind(text: str) -> str:
    """Return affirm, decline, or empty for a reply that only continues the last question."""

    token = "".join(text.strip().strip("။.!?၊ ").split())
    folded = token.casefold()
    if token in _DECLINES or folded in _DECLINES:
        return "decline"
    if token in _AFFIRMATIONS or folded in _AFFIRMATIONS:
        return "affirm"
    return ""


def _notice_code(body: Mapping[str, object]) -> str:
    error = body.get("error")
    if isinstance(error, str) and error:
        return error
    status = body.get("status")
    return status if isinstance(status, str) else ""


def _stored_status(body: Mapping[str, object]) -> str:
    if not isinstance(body.get("error"), str) or not body.get("error"):
        return ""
    status = body.get("status")
    return status if isinstance(status, str) else ""


def recovery_sentence(
    body: Mapping[str, object],
    staff_text: str,
    *,
    can_continue: bool = True,
) -> str:
    """Say what happened and the one next action. No tool name, id, or screen."""

    code = _notice_code(body)
    status = _stored_status(body)
    if code in {"finance_state", "not_pending", "payment_state"} and status in _STATUS_NEXT:
        pair = (
            _PENDING_OTHER
            if status == "PENDING_APPROVAL" and not can_continue
            else _STATUS_NEXT[status]
        )
        return _sentence(pair, staff_text)
    if code in _GATE_SENTENCES:
        return gate_sentence(code, staff_text, body)
    return _sentence(_RECOVERY_FALLBACK, staff_text)


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
    if error in {"finance_state", "not_pending", "payment_state", "assignment_required"}:
        return recovery_sentence(body, staff_text)
    if error not in _NOTICE_ERRORS:
        return None
    burmese = speaks_burmese(staff_text)
    if error == "permission_denied":
        return (
            "ဒီအချက်အလက်ကို ကြည့်ခွင့် မရှိပါ။ ခွင့်ရှိတဲ့ အချက်ကို မေးပါ။"
            if burmese
            else "You cannot view that information. Ask for something you can view."
        )
    if error == "unavailable":
        return (
            "မပြီးမြောက်ပါ။ ဘာမှ မပြောင်းရသေးပါ။ နောက်တစ်ဆင့်ကို ပြန်ပြောပေးပါ။"
            if burmese
            else "I could not complete that. Nothing was changed. Repeat the step you want."
        )
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
    keep_prose = not stored
    pieces: list[str] = []
    for marker, content in _list_groups(reply):
        grounded = _ground_piece(
            content,
            allowed_text=allowed_text,
            stored=stored,
            keep_prose=keep_prose,
        )
        if _is_list_debris(grounded):
            continue
        pieces.append(f"{marker}{grounded}".strip() if marker else grounded)
    body = _tidy_field_labels(_tidy_list(" ".join(pieces)))
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
        sentence = _readable_field_labels(_without_tool_names(sentence))
        if not sentence or _leaks(sentence):
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
        body = _tidy_field_labels(" ".join(kept).strip())
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


def unconfirmed_notice(staff_text: str) -> str:
    """State that a prepared change is still waiting. This is not a success."""

    return _unconfirmed_sentence(speaks_burmese(staff_text))


_ABSENCE = re.compile(
    r"\b(?:there (?:are|is|aren't|isn't) (?:currently )?(?:no |none |not any )?"
    r"(?:[\w-]+ ){0,4}?"
    r"(?:trainer|member|assignment|payment|class|expense|sale|announcement|"
    r"package|product|record|session)s?\b|"
    r"\bno (?:[\w-]+ ){0,3}?"
    r"(?:trainer|member|assignment|payment|class|expense|sale|announcement|"
    r"package|product|record|session)s?\b|"
    r"\bnone (?:are|currently|in)\b|\bnot assigned\b)",
    re.IGNORECASE,
)
_ABSENCE_MY = ("မရှိပါ", "မရှိဘူး", "မရှိသေး", "မတွေ့ပါ", "မတွေ့ဘူး", "ရှိပါတယ်", "ရှိပါသည်")
_NEGATIVE_MY = ("မရှိပါ", "မရှိဘူး", "မရှိသေး", "မတွေ့ပါ", "မတွေ့ဘူး")
_NEGATIVE_ABSENCE = re.compile(
    r"\b(?:there (?:are|is|aren't|isn't) (?:currently )?(?:no |none |not any )"
    r"(?:[\w-]+ ){0,6}?"
    r"(?:trainer|member|assignment|payment|class|expense|sale|announcement|"
    r"package|product|record|session)s?\b|"
    r"\bno (?:[\w-]+ ){0,4}?"
    r"(?:trainer|member|assignment|payment|class|expense|sale|announcement|"
    r"package|product|record|session)s?\b|"
    r"\bnone (?:are|currently|in)\b|\bnot assigned\b)",
    re.IGNORECASE,
)
_PRESENCE_CUES = ("no", "none", "currently", "active", "assigned", "မရှိ", "ရှိ")
_RECORD_CUES = (
    "member",
    "trainer",
    "assignment",
    "payment",
    "class",
    "expense",
    "sale",
    "package",
    "product",
    "announcement",
    "staff",
    "booking",
    "session",
    "price",
    "revenue",
    "အဖွဲ့ဝင်",
    "သင်တန်း",
    "တာဝန်",
    "ငွေ",
    "အတန်း",
    "အသုံး",
    "အရောင်း",
    "ပက်ကေ့",
    "ပစ္စည်း",
    "လစာ",
    "ကြေငြာ",
    "ဈေး",
)


def states_stored_facts(text: str) -> bool:
    """A reply that asserts what the gym has stored, not a definition."""

    if not text.strip():
        return False
    if _absence_claim(text):
        return True
    if _names(text):
        return True
    translated = exact_code(text)
    if any(
        "." in match.group(0) or "," in match.group(0)
        for match in _CLAIM_NUMBER.finditer(translated)
    ):
        return True
    if _CLAIM_DATE.search(translated):
        return True
    folded = translated.casefold()
    for match in _CLAIM_NUMBER.finditer(translated):
        if _inside_date(translated, match.start()):
            continue
        start = max(0, match.start() - 48)
        end = min(len(translated), match.end() + 48)
        window = folded[start:end]
        original = text[max(0, start) : min(len(text), end)]
        if not any(cue in window or cue in original for cue in _PRESENCE_CUES):
            continue
        if any(
            re.search(rf"\b{re.escape(word)}\b", window) or word in original
            for word in _RECORD_CUES
        ):
            return True
    return False


def _absence_claim(text: str) -> bool:
    return _ABSENCE.search(text) is not None or any(phrase in text for phrase in _ABSENCE_MY)


def _negative_absence(text: str) -> bool:
    """The reply says a stored record is missing, not merely that one exists."""

    return _NEGATIVE_ABSENCE.search(text) is not None or any(
        phrase in text for phrase in _NEGATIVE_MY
    )


def reply_needs_fresh_read(question: str, reply: str, allowed: str) -> bool:
    """A stored-data answer needs a read when this turn has not shown those facts.

    A date or amount already in the gym brief or the staff's words does not.
    Saying that a record is absent still does, because a total in the brief is
    not the list the question asked for.
    """

    if not information_question(question) or not states_stored_facts(reply):
        return False
    if _negative_absence(reply):
        return True
    allowed_text = _with_equivalent_dates(allowed)
    sentences = [sentence for sentence in _reply_sentences(reply) if sentence.strip()]
    if not sentences:
        return False
    return any(not _sentence_supported(sentence, allowed_text) for sentence in sentences)


_PROPOSAL_LABELS: dict[str, str] = {
    "Create a member": "အဖွဲ့ဝင်အသစ် ဖွင့်မယ်။",
    "Update a member's name, phone, email, or notes": "အဖွဲ့ဝင်ရဲ့ နာမည်၊ ဖုန်း၊ အီးမေးလ် သို့မဟုတ် မှတ်ချက် ပြင်မယ်။",
    "Archive a member": "အဖွဲ့ဝင်ကို မှတ်တမ်းသိမ်းမယ်။",
    "Restore a member": "အဖွဲ့ဝင်ကို ပြန်ဖွင့်မယ်။",
    "Create a membership plan": "အသင်းဝင်အစီအစဉ် ဖွင့်မယ်။",
    "Update a membership plan's name, duration, or price": (
        "အသင်းဝင်အစီအစဉ်ရဲ့ နာမည်၊ သက်တမ်း သို့မဟုတ် ဈေးနှုန်း ပြင်မယ်။"
    ),
    "Archive a membership plan": "အသင်းဝင်အစီအစဉ်ကို မှတ်တမ်းသိမ်းမယ်။",
    "Create a membership": "အသင်းဝင်ကြေး ဖွင့်မယ်။",
    "Update a membership note": "အသင်းဝင်ကြေး မှတ်ချက် ပြင်မယ်။",
    "Suspend a membership": "အသင်းဝင်ကြေးကို ခေတ္တရပ်မယ်။",
    "Resume a membership": "အသင်းဝင်ကြေးကို ပြန်စမယ်။",
    "Cancel a membership": "အသင်းဝင်ကြေးကို ပယ်ဖျက်မယ်။",
    "Create a draft announcement": "ကြေငြာချက် မူကြမ်း ဖွင့်မယ်။",
    "Update a draft announcement": "ကြေငြာချက် မူကြမ်း ပြင်မယ်။",
    "Publish or schedule an announcement": "ကြေငြာချက်ကို ထုတ်ပြန်မယ် သို့မဟုတ် အချိန်သတ်မှတ်မယ်။",
    "Cancel an announcement": "ကြေငြာချက်ကို ပယ်ဖျက်မယ်။",
    "Record a manual check-in": "လက်ဖြင့် ဝင်ရောက်မှု မှတ်မယ်။",
    "Check out an open visit": "ဖွင့်ထားတဲ့ ဝင်ရောက်မှုကနေ ထွက်မယ်။",
    "Book a member into one scheduled class occurrence": "အဖွဲ့ဝင်ကို အတန်းတစ်ခုမှာ ဘိုကင်လုပ်မယ်။",
    "Mark an existing class booking attended": "အတန်းဘိုကင်ကို တက်ရောက်ပြီး မှတ်မယ်။",
    "Mark an existing class booking as a no-show after that class has ended": (
        "အတန်းဘိုကင်ကို မတက်ဘူးလို့ မှတ်မယ်။"
    ),
    "Cancel an existing class booking that is still booked": "အတန်းဘိုကင်ကို ပယ်ဖျက်မယ်။",
    "Open a cash session": "ငွေသားစာရင်း ဖွင့်မယ်။",
    "Adjust a cash session": "ငွေသားစာရင်း ပြင်မယ်။",
    "Reconcile a cash session": "ငွေသားစာရင်း ညှိမယ်။",
    "Create an expense category": "အသုံးစရိတ် အမျိုးအစား ဖွင့်မယ်။",
    "Update an expense category": "အသုံးစရိတ် အမျိုးအစား ပြင်မယ်။",
    "Create an expense draft": "အသုံးစရိတ် မူကြမ်း ဖွင့်မယ်။",
    "Update an expense draft": "အသုံးစရိတ် မူကြမ်း ပြင်မယ်။",
    "Submit an expense draft": "အသုံးစရိတ် မူကြမ်း တင်မယ်။",
    "Cancel an expense draft": "အသုံးစရိတ် မူကြမ်း ပယ်ဖျက်မယ်။",
    "Void a posted expense": "တင်ပြီး အသုံးစရိတ်ကို ပယ်ဖျက်မယ်။",
    "Approve a submitted expense that is waiting for approval": "တင်ထားတဲ့ အသုံးစရိတ်ကို အတည်ပြုမယ်။",
    "Reject a submitted expense that is waiting for approval": "တင်ထားတဲ့ အသုံးစရိတ်ကို ငြင်းမယ်။",
    "Create a product category": "ပစ္စည်းအမျိုးအစား ဖွင့်မယ်။",
    "Create a product": "ပစ္စည်း ဖွင့်မယ်။",
    "Update a product's name or price": "ပစ္စည်းရဲ့ နာမည် သို့မဟုတ် ဈေးနှုန်း ပြင်မယ်။",
    "Adjust stock": "လက်ကျန် ပြင်မယ်။",
    "Create a member payment draft": "အဖွဲ့ဝင် ငွေပေးချေမှု မူကြမ်း ဖွင့်မယ်။",
    "Update a payment draft": "ငွေပေးချေမှု မူကြမ်း ပြင်မယ်။",
    "Submit a payment draft": "ငွေပေးချေမှု မူကြမ်း တင်မယ်။",
    "Cancel a payment draft": "ငွေပေးချေမှု မူကြမ်း ပယ်ဖျက်မယ်။",
    "Void a posted payment": "တင်ပြီး ငွေပေးချေမှုကို ပယ်ဖျက်မယ်။",
    "Refund part of a posted charge": "တင်ပြီး ကျသင့်ငွေရဲ့ တစ်စိတ်တစ်ပိုင်း ပြန်အမ်းမယ်။",
    "Void an issued receipt": "ထုတ်ပြီး ပြေစာကို ပယ်ဖျက်မယ်။",
    "Approve a submitted payment that is waiting for approval": "တင်ထားတဲ့ ငွေပေးချေမှုကို အတည်ပြုမယ်။",
    "Reject a submitted payment that is waiting for approval": "တင်ထားတဲ့ ငွေပေးချေမှုကို ငြင်းမယ်။",
    "Create a PT sale draft": "PT အရောင်း မူကြမ်း ဖွင့်မယ်။",
    "Update a PT sale draft": "PT အရောင်း မူကြမ်း ပြင်မယ်။",
    "Cancel a PT sale draft": "PT အရောင်း မူကြမ်း ပယ်ဖျက်မယ်။",
    "Submit a PT sale draft": "PT အရောင်း မူကြမ်း တင်မယ်။",
    "Void a posted PT sale": "တင်ပြီး PT အရောင်းကို ပယ်ဖျက်မယ်။",
    "Record a PT commission payout": "PT ကော်မရှင် ထုတ်ပေးမှု မှတ်မယ်။",
    "Grant a member service package": "အဖွဲ့ဝင်ကို ပက်ကေ့ချ် ပေးမယ်။",
    "Correct a member package ledger entry": "အဖွဲ့ဝင် ပက်ကေ့ချ် မှတ်တမ်း ပြင်မယ်။",
    "Create a PT booking": "PT ဘိုကင် ဖွင့်မယ်။",
    "Attend a PT booking": "PT ဘိုကင်ကို တက်ရောက်ပြီး မှတ်မယ်။",
    "Mark a PT booking no-show": "PT ဘိုကင်ကို မတက်ဘူးလို့ မှတ်မယ်။",
    "Cancel a PT booking": "PT ဘိုကင်ကို ပယ်ဖျက်မယ်။",
    "Approve a submitted PT sale that is waiting for approval": "တင်ထားတဲ့ PT အရောင်းကို အတည်ပြုမယ်။",
    "Reject a submitted PT sale that is waiting for approval": "တင်ထားတဲ့ PT အရောင်းကို ငြင်းမယ်။",
    "Create a retail sale draft": "လက်လီအရောင်း မူကြမ်း ဖွင့်မယ်။",
    "Update a retail sale draft": "လက်လီအရောင်း မူကြမ်း ပြင်မယ်။",
    "Submit a retail sale draft": "လက်လီအရောင်း မူကြမ်း တင်မယ်။",
    "Cancel a retail sale draft": "လက်လီအရောင်း မူကြမ်း ပယ်ဖျက်မယ်။",
    "Void a posted retail sale": "တင်ပြီး လက်လီအရောင်းကို ပယ်ဖျက်မယ်။",
    "Return part of a posted retail sale": "တင်ပြီး လက်လီအရောင်းရဲ့ တစ်စိတ်တစ်ပိုင်း ပြန်အမ်းမယ်။",
    "Approve a submitted retail sale that is waiting for approval": "တင်ထားတဲ့ လက်လီအရောင်းကို အတည်ပြုမယ်။",
    "Reject a submitted retail sale that is waiting for approval": "တင်ထားတဲ့ လက်လီအရောင်းကို ငြင်းမယ်။",
    "Create a salary period draft for staff wages over a date range": "ဝန်ထမ်းလစာ ကာလ မူကြမ်း ဖွင့်မယ်။",
    "Update a salary period draft": "လစာကာလ မူကြမ်း ပြင်မယ်။",
    "Replace salary period lines": "လစာကာလ စာရင်းတွေ အစားထိုးမယ်။",
    "Submit a salary period draft": "လစာကာလ မူကြမ်း တင်မယ်။",
    "Cancel a salary period draft": "လစာကာလ မူကြမ်း ပယ်ဖျက်မယ်။",
    "Void a posted salary period": "တင်ပြီး လစာကာလကို ပယ်ဖျက်မယ်။",
    "Approve a submitted salary period that is waiting for approval": "တင်ထားတဲ့ လစာကာလကို အတည်ပြုမယ်။",
    "Reject a submitted salary period that is waiting for approval": "တင်ထားတဲ့ လစာကာလကို ငြင်းမယ်။",
    "Create a class schedule": "အတန်းအချိန်ဇယား ဖွင့်မယ်။",
    "Update a class schedule": "အတန်းအချိန်ဇယား ပြင်မယ်။",
    "Cancel a class occurrence": "အတန်းတစ်ကြိမ်ကို ပယ်ဖျက်မယ်။",
    "Update a staff profile": "ဝန်ထမ်းပရိုဖိုင် ပြင်မယ်။",
    "Activate a suspended staff membership": "ရပ်ထားတဲ့ ဝန်ထမ်းကို ပြန်ဖွင့်မယ်။",
    "Suspend an active staff membership": "ဝန်ထမ်းကို ခေတ္တရပ်မယ်။",
    "Create a custom role": "အခန်းကဏ္ဍ ဖွင့်မယ်။",
    "Update a custom role": "အခန်းကဏ္ဍ ပြင်မယ်။",
    "Archive a custom role": "အခန်းကဏ္ဍကို မှတ်တမ်းသိမ်းမယ်။",
    "Replace a role's permissions": "အခန်းကဏ္ဍရဲ့ ခွင့်ပြုချက်တွေ အစားထိုးမယ်။",
    "Replace a staff membership's roles": "ဝန်ထမ်းရဲ့ အခန်းကဏ္ဍတွေ အစားထိုးမယ်။",
    "Update the gym profile": "Gym ပရိုဖိုင် ပြင်မယ်။",
    "Update gym settings": "Gym ဆက်တင် ပြင်မယ်။",
    "Revoke another active owner": "တက်ကြွတဲ့ ပိုင်ရှင်တစ်ဦးကို ပယ်ဖျက်မယ်။",
    "Cancel a pending owner invitation": "စောင့်ဆိုင်းနေတဲ့ ပိုင်ရှင်ဖိတ်ကြားချက်ကို ပယ်ဖျက်မယ်။",
    "Assign a trainer to a member": "အဖွဲ့ဝင်ကို သင်တန်းဆရာ တာဝန်ပေးမယ်။",
    "End an active trainer assignment": "သင်တန်းဆရာ တာဝန်ပေးမှုကို အဆုံးသတ်မယ်။",
    "Cancel an active trainer assignment": "သင်တန်းဆရာ တာဝန်ပေးမှုကို ပယ်ဖျက်မယ်။",
    "Create a service package": "ဝန်ဆောင်မှု ပက်ကေ့ချ် ဖွင့်မယ်။",
    "Create a class type": "အတန်းအမျိုးအစား ဖွင့်မယ်။",
    "Update one class type": "အတန်းအမျိုးအစား ပြင်မယ်။",
}
_SUMMARY_AMOUNT = re.compile(r"^(?P<body>.*?) Amount (?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\.$")
_SUMMARY_RECORD = re.compile(r"^(?P<body>.*?) Record (?P<record>\S+), amount (?P<amount>\S+)\.$")


def localized_summary(summary: str, *, burmese: bool) -> str:
    """Translate a proposal summary. Amounts and record ids stay as stored."""

    if not burmese:
        return summary
    text = " ".join(summary.split())
    suffix = ""
    body = text
    recorded = _SUMMARY_RECORD.match(text)
    amount = _SUMMARY_AMOUNT.match(text)
    if recorded:
        body = recorded.group("body").strip()
        suffix = f" မှတ်တမ်း {recorded.group('record')}၊ ပမာဏ {recorded.group('amount')}။"
    elif amount:
        body = amount.group("body").strip()
        suffix = f" ပမာဏ {amount.group('amount')}။"
    label = _proposal_label(body)
    if label is None:
        return summary
    return f"{label}{suffix}".strip()


def _proposal_label(body: str) -> str | None:
    key = body.strip().removesuffix(".")
    found = _PROPOSAL_LABELS.get(key)
    if found:
        return found
    return _PROPOSAL_LABELS.get(key.split(". ", 1)[0])


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


def _ground_piece(
    text: str,
    *,
    allowed_text: str,
    stored: tuple[Mapping[str, object], ...],
    keep_prose: bool,
) -> str:
    """Keep supported sentences. Drop a fragment that is only a marker or parenthesis."""

    sentences: list[str] = []
    for sentence in _reply_sentences(text):
        if _leaks(sentence):
            continue
        cleaned = _readable_field_labels(_without_tool_names(sentence))
        if not cleaned or _leaks(cleaned):
            continue
        aligned = _align_financial_claims(cleaned, stored)
        corrected = _without_gutted_examples(
            _correct_sentence(aligned, allowed_text, keep_prose=keep_prose)
        )
        if corrected and not _leaks(corrected) and not _is_list_debris(corrected):
            sentences.append(corrected)
    return " ".join(sentences).strip()


_LIST_NUMBERS = "၀၁၂၃၄၅၆၇၈၉"
_NUMBERED = rf"[0-9{_LIST_NUMBERS}]{{1,2}}"
_MARKER_ONLY = re.compile(rf"^(?:{_NUMBERED}\s*[.)။၊:]|[•*]\s*|-\s*)\s*[။.]*\s*$")
_LEADING_MARKER = re.compile(rf"^(?P<marker>(?:{_NUMBERED}\s*[.)။၊:]\s*|[•*]\s+|-\s+))")
_GUTTED_EXAMPLE = re.compile(
    r"\s*\(\s*(?:ဥပမာ|example|e\.g\.|for example|such as|မှတ်ချက်)?\s*[-:]*\s*\)",
    re.IGNORECASE,
)
_EMPTY_MARKER = re.compile(
    rf"(?:(?<=\s)|^)(?:{_NUMBERED}[.)။၊](?!\d)\s*|[•*]\s+|-\s+)"
    rf"(?=\s*(?:[။၊]|$|{_NUMBERED}[.)။၊](?!\d)|[•*]|-\s))"
)
_CHUNK_AT_MARKER = re.compile(
    rf"(?:(?<=\s)|(?<=^))(?={_NUMBERED}[.)။၊](?!\d)\s)"
    rf"|(?:(?<=\n)|(?<=^))(?=[•*]\s+|-\s+)"
)
_ORPHAN_PAREN = re.compile(
    rf"(?:(?<=^)|(?<=[။.!?]\s))\s*(?:{_NUMBERED}[.)။၊]\s*)?\([^)]*\)\s*[။.]?"
)


def _list_groups(text: str) -> list[tuple[str, str]]:
    """Pair a list marker with its item. A removed item takes its marker with it."""

    groups: list[tuple[str, str]] = []
    for chunk in _marker_chunks(text):
        groups.extend(_groups_in_chunk(chunk))
    return groups


def _marker_chunks(text: str) -> list[str]:
    """Cut before a list marker so a number is not stripped as an ordinary claim."""

    starts = [match.start() for match in _CHUNK_AT_MARKER.finditer(text)]
    points = [0, *[start for start in starts if start > 0], len(text)]
    return [text[points[index] : points[index + 1]] for index in range(len(points) - 1)]


def _groups_in_chunk(text: str) -> list[tuple[str, str]]:
    groups: list[tuple[str, str]] = []
    sentences = _reply_sentences(text)
    index = 0
    while index < len(sentences):
        marker = _marker_only(sentences[index])
        if marker is not None:
            nxt = sentences[index + 1] if index + 1 < len(sentences) else ""
            if nxt and _marker_only(nxt) is None:
                groups.append((marker, nxt))
                index += 2
                continue
            groups.append((marker, ""))
            index += 1
            continue
        lead, rest = _split_leading_marker(sentences[index])
        groups.append((lead, rest))
        index += 1
    return groups


def _marker_only(sentence: str) -> str | None:
    text = sentence.strip()
    if _MARKER_ONLY.fullmatch(text) is None:
        return None
    return text if text.endswith(" ") else f"{text} "


def _split_leading_marker(sentence: str) -> tuple[str, str]:
    text = sentence.strip()
    match = _LEADING_MARKER.match(text)
    if match is None:
        return "", text
    return match.group("marker"), text[match.end() :]


def _without_gutted_examples(text: str) -> str:
    if not text:
        return ""
    return " ".join(_GUTTED_EXAMPLE.sub(" ", text).split())


def _is_list_debris(text: str) -> bool:
    """A marker, a lone stop, or a parenthesis with no sentence left around it."""

    stripped = text.strip()
    if not stripped:
        return True
    lead, rest = _split_leading_marker(stripped)
    body = rest if lead else stripped
    body = body.strip("။၊.!? \t")
    if not body:
        return True
    outside = re.sub(r"\([^)]*\)", " ", body)
    return not _has_prose(outside)


def _tidy_list(text: str) -> str:
    """Drop empty markers, repeated stops, and a parenthesis left by itself."""

    cleaned = _ORPHAN_PAREN.sub(" ", text)
    previous = ""
    while cleaned != previous:
        previous = cleaned
        cleaned = _EMPTY_MARKER.sub(" ", cleaned)
    cleaned = re.sub(r"(?:(?<=\s)|^)[။၊]+(?=\s|$)", " ", cleaned)
    cleaned = re.sub(r"(?:[။၊]\s*){2,}", "။ ", cleaned)
    return " ".join(cleaned.split())


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


def _correct_sentence(sentence: str, allowed_text: str, *, keep_prose: bool = False) -> str:
    """Return the sentence, a trimmed sentence, or empty when its facts are gone."""

    if _sentence_supported(sentence, allowed_text):
        return sentence
    corrected = _drop_unsupported_claims(sentence, allowed_text)
    if not corrected or not _sentence_supported(corrected, allowed_text):
        return ""
    if _sentence_facts(sentence) and not _sentence_facts(corrected):
        if (
            keep_prose
            and _has_prose(corrected)
            and _unsupported_are_only_numbers(sentence, allowed_text)
        ):
            return corrected
        return ""
    return corrected


def _unsupported_are_only_numbers(sentence: str, allowed_text: str) -> bool:
    """An example count can leave a definition. An invented name or date cannot."""

    if any(not _name_supported(name, allowed_text) for name in _names(sentence)):
        return False
    extra = _claim_tokens(sentence) - _claim_tokens(allowed_text)
    numbers = {
        _canon_number(match.group(0)) for match in _CLAIM_NUMBER.finditer(exact_code(sentence))
    }
    return bool(extra) and extra <= numbers


def _has_prose(text: str) -> bool:
    return any(char.isalpha() or "\u1000" <= char <= "\u109f" for char in text)


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


def _mentions_pt_sale(text: str) -> bool:
    folded = text.casefold()
    if "package" in folded or "ပက်ကေ့" in text:
        return False
    return ("pt" in folded or "personal training" in folded) and (
        "sale" in folded or "အရောင်း" in text
    )


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


def _asks(text: str) -> bool:
    if _ASKS.search(text):
        return True
    return any(phrase in text for phrase in _ASK_PHRASES)


def _is_change_request(text: str) -> bool:
    folded = text.casefold()
    if any(phrase in text or phrase in folded for phrase in _WRITE_PHRASES):
        return True
    return _WRITE_WORDS.search(text) is not None


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


_BOLD_LABEL = re.compile(r"\*\*([^*]+?):\*\*")
_FIELD_WORDS = frozenset(
    {
        "active",
        "barcode",
        "category",
        "cost",
        "current",
        "default",
        "hand",
        "inventory",
        "level",
        "low",
        "name",
        "on",
        "price",
        "product",
        "quantity",
        "reorder",
        "sale",
        "sku",
        "status",
        "stock",
        "threshold",
        "unit",
        "value",
    }
)
_EMPTY_BOLD = re.compile(r"\*\*\s*:?\s*\*\*")
_VALUELESS_LABEL = re.compile(r"\*\*[^*]+?:\*\*(?=\s*(?:\*\*|$))")


def _caption_words(label: str) -> list[str]:
    text = label.strip()
    if "_" in text and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", text):
        return [part for part in text.split("_") if part]
    return re.findall(r"[A-Za-z]+", text)


def _field_caption(label: str) -> bool:
    """A stored-field caption, not a person's name."""

    words = _caption_words(label)
    return bool(words) and all(word.casefold() in _FIELD_WORDS for word in words)


def _readable_field_labels(text: str) -> str:
    """Show sale_price as Sale price. A person's name is not a field caption."""

    def replace(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        if "_" not in label or not _field_caption(label):
            return match.group(0)
        words = [part for part in label.split("_") if part]
        pretty = " ".join(words)
        return f"**{pretty[:1].upper()}{pretty[1:]}:**"

    return _BOLD_LABEL.sub(replace, text)


def _tidy_field_labels(text: str) -> str:
    """Drop **:** and a label whose value was removed."""

    cleaned = _EMPTY_BOLD.sub(" ", text)
    previous = ""
    while cleaned != previous:
        previous = cleaned
        cleaned = _VALUELESS_LABEL.sub(" ", cleaned)
    return " ".join(cleaned.split())


def _names(text: str) -> set[str]:
    coded = exact_code(text)
    captions = [
        match.span(1) for match in _BOLD_LABEL.finditer(coded) if _field_caption(match.group(1))
    ]
    found: set[str] = set()
    for match in _CLAIM_NAME.finditer(coded):
        if any(start <= match.start() and match.end() <= end for start, end in captions):
            continue
        found.add(match.group(0))
    return found


def _name_supported(name: str, allowed_text: str) -> bool:
    if _has_term(name, allowed_text):
        return True
    return all(_has_term(word, allowed_text) for word in name.split())


def _has_term(term: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


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
    """True when the sentence contains an internal error or database phrase."""

    folded = reply.casefold()
    return any(token in folded for token in _LEAK_TEXT)


def _without_tool_names(text: str) -> str:
    """Remove registered tool names. A stored code such as Monthly_plan stays."""

    if "_" not in text:
        return text
    from apps.assistant.registry import production_registry

    cleaned = text
    names = sorted(production_registry().names(), key=len, reverse=True)
    for name in names:
        cleaned = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            "",
            cleaned,
        )
    return " ".join(cleaned.split())

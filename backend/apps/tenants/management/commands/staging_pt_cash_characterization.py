"""Bounded cash PT commission payout and till characterization.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and sends
fixed requests through Caddy. It prints metric JSON only. It does not prove a
load test, soak, capacity, SLO, or financial reconciliation.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as clock_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.db.models import Sum

from apps.cashbook.models import CashSession, CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.common.load_characterization import (
    assert_metrics_target,
    caddy_metrics_observation,
    marker_present,
    material_leaked,
    percentile_ms,
)
from apps.common.pt_cash_characterization import (
    COMMISSION_AMOUNT,
    COVERAGE,
    DIFFERENCE,
    EXPECTED_CLOSING,
    GROSS_FEE,
    OPENING_CASH,
    PROFILE_NAME,
    PROOF_FLAGS,
    REGISTER_CODE,
    assert_pt_cash_path,
    assert_rehearsal_context,
    assess_pt_cash,
    build_plan,
    classify_status,
    failure_document,
    judge,
    make_key,
    public_outcome,
    resolve_profile,
    same_gym_link_mismatches,
    scenarios_match,
    summarize_scenario,
)
from apps.expenses.models import Expense
from apps.members.models import Member
from apps.members.services import create_member, gym_today
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    LoadError,
    _json_body,
    _login_ok,
    _nested_id,
    _postgres_peak,
    _refresh_csrf,
    _resolve,
    _sample_postgres,
)
from apps.tenants.models import ExpenseCategory, GymMembership, Role, StaffProfile
from apps.tenants.services import (
    _create_pending_owner_invitation,
    accept_owner_invitation,
    provision_gym,
)
from apps.tenants.staff_services import accept_staff_invitation, invite_staff
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServicePackage,
    ServiceSessionLedger,
)
from apps.trainers.services import create_assignment, create_service_package

_REQUIRED_SETTINGS = "config.settings.staging_rehearsal"
_REQUIRED_PROJECT = "gym-staging-rehearsal"
_REFUSED_LIVE_PROOF = (
    "load_test_proven",
    "soak_test_proven",
    "production_capacity_proven",
    "slo_proven",
    "financial_reconciliation_proven",
)
_GYMS = (
    ("PTCASHA", "PT Cash A", "owner-cash-a@rehearsal.invalid", "manager-cash-a@rehearsal.invalid"),
    ("PTCASHB", "PT Cash B", "owner-cash-b@rehearsal.invalid", "manager-cash-b@rehearsal.invalid"),
)
_MONEY = re.compile(r"\A\d+\.\d{2}\Z")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class PtError(RuntimeError):
    """The PT characterization cannot continue."""


@dataclass
class GymPt:
    index: int
    gym_id: UUID
    gym_code: str
    owner_email: str
    owner_password: str
    approver_email: str
    approver_password: str
    business_date: str
    member_id: str
    trainer_id: str
    package_id: str
    starts_at: str
    ends_at: str
    passwords: list[str] = field(default_factory=list)
    owner_jar: Jar = field(default_factory=Jar)
    approver_jar: Jar = field(default_factory=Jar)
    sale_id: str = ""
    booking_id: str = ""
    commission_id: str = ""
    grant_id: str = ""
    cash_id: str = ""


def _safe_text(exc: BaseException) -> str:
    text = type(exc).__name__
    message = str(exc)
    if "@" in message or "password" in message.lower() or "token" in message.lower():
        return text
    if len(message) > 180:
        return text
    return f"{text}: {message}" if message else text


def _walk(payload: bytes, path: tuple[str, ...]) -> object:
    try:
        current: object = json.loads(payload)
    except json.JSONDecodeError:
        return None
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _text(payload: bytes, path: tuple[str, ...]) -> str:
    value = _walk(payload, path)
    if not isinstance(value, str):
        return ""
    return value


def _money(payload: bytes, path: tuple[str, ...]) -> str:
    value = _text(payload, path)
    if _MONEY.fullmatch(value) is None:
        return ""
    return value


def _count_field(payload: bytes, path: tuple[str, ...]) -> int | None:
    value = _walk(payload, path)
    if type(value) is int:
        return value
    return None


def _install_owner(membership: GymMembership, email: str, password: str) -> GymMembership:
    with transaction.atomic():
        set_gym_context(gym_id=membership.gym_id)
        _invitation, token = _create_pending_owner_invitation(
            gym=membership.gym,
            membership=membership,
            email=email,
            normalized_email=email,
            inviter_membership_id=membership.id,
        )
    try:
        return accept_owner_invitation(
            plaintext_token=token,
            password=password,
            request_id="pt-seed",
            ip_address=None,
            user_agent="pt",
        )
    finally:
        del token


def _staff_token() -> str:
    box = getattr(mail, "outbox", None)
    if not isinstance(box, list) or not box:
        raise PtError("staff invitation was not captured")
    message = box[-1].body
    if not isinstance(message, str) or "token=" not in message:
        raise PtError("staff invitation was not captured")
    token = message.split("token=", 1)[1].split()[0].strip()
    if len(token) < 16:
        raise PtError("staff invitation was not captured")
    return token


def _install_staff(
    owner: GymMembership,
    *,
    email: str,
    password: str,
    role_key: str,
    staff_code: str,
    first_name: str,
    last_name: str,
    display_name: str,
    title: str,
    commission: Decimal,
) -> str:
    with transaction.atomic():
        set_gym_context(gym_id=owner.gym_id)
        role = Role.objects.get(
            gym_id=owner.gym_id,
            key=role_key,
            is_active=True,
            source_template__key=role_key,
        )
        today = gym_today(owner.gym)
    invite_staff(
        gym_id=owner.gym_id,
        actor=owner.user,
        actor_membership_id=owner.id,
        staff_data={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "staff_code": staff_code,
            "display_name": display_name,
            "joined_date": today,
            "employment_title": title,
            "default_commission_percent": commission,
        },
        role_ids=[role.id],
        request_id="pt-seed",
        user_agent="pt",
    )
    token = _staff_token()
    try:
        membership = accept_staff_invitation(
            plaintext_token=token,
            password=password,
            confirmation={
                "display_name": display_name,
                "first_name": first_name,
                "last_name": last_name,
            },
            request_id="pt-seed",
            user_agent="pt",
        )
    finally:
        del token
        box = getattr(mail, "outbox", None)
        if isinstance(box, list):
            box.clear()
    with transaction.atomic():
        set_gym_context(gym_id=owner.gym_id)
        profile = StaffProfile.objects.get(gym_id=owner.gym_id, gym_membership_id=membership.id)
        return str(profile.id)


def _seed() -> list[GymPt]:
    states: list[GymPt] = []
    for index, (code, name, owner_email, approver_email) in enumerate(_GYMS):
        owner_password = secrets.token_urlsafe(24)
        approver_password = secrets.token_urlsafe(24)
        trainer_password = secrets.token_urlsafe(24)
        trainer_email = f"trainer-cash-{code[-1].lower()}@rehearsal.invalid"
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=owner_email)
            owner = _install_owner(provisioned.owner_membership, owner_email, owner_password)
            _install_staff(
                owner,
                email=approver_email,
                password=approver_password,
                role_key="MANAGER",
                staff_code=f"PTCMA{code[-1]}",
                first_name="Load",
                last_name="Approver",
                display_name="Load Approver",
                title="Manager",
                commission=Decimal("0"),
            )
            trainer_id = _install_staff(
                owner,
                email=trainer_email,
                password=trainer_password,
                role_key="TRAINER",
                staff_code=f"PTCTA{code[-1]}",
                first_name="Load",
                last_name="Trainer",
                display_name="Load Trainer",
                title="Trainer",
                commission=Decimal("10"),
            )
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                today = gym_today(provisioned.gym)
                member = create_member(
                    gym=provisioned.gym,
                    actor=owner,
                    member_number="",
                    first_name="Load",
                    last_name="Member",
                    phone=None,
                    email=None,
                    gender=None,
                    date_of_birth=None,
                    address={},
                    join_date=today,
                    emergency_contact_name="",
                    emergency_contact_phone="",
                    notes="",
                    profile_photo_asset_id=None,
                )
                package = create_service_package(
                    gym=provisioned.gym,
                    actor=owner,
                    package_type=ServicePackage.PackageType.PT,
                    code="PT10",
                    name="PT Ten",
                    session_count=1,
                    validity_value=30,
                    validity_unit=ServicePackage.ValidityUnit.DAY,
                    price=Decimal("0.00"),
                    request_id="pt-seed",
                    ip_address=None,
                    user_agent="pt",
                )
                create_assignment(
                    gym=provisioned.gym,
                    actor=owner,
                    trainer_staff_profile_id=UUID(trainer_id),
                    member_id=member.id,
                    effective_from=today,
                    effective_until=None,
                    reason="PT characterization",
                    request_id="pt-seed",
                    ip_address=None,
                    user_agent="pt",
                )
            zone = ZoneInfo(provisioned.gym.timezone)
            start = datetime.combine(today, clock_time(10, 0), tzinfo=zone)
            end = datetime.combine(today, clock_time(11, 0), tzinfo=zone)
        except Exception as exc:
            raise PtError(f"staging pt seed failed: {_safe_text(exc)}") from None
        states.append(
            GymPt(
                index=index,
                gym_id=provisioned.gym.id,
                gym_code=code,
                owner_email=owner_email,
                owner_password=owner_password,
                approver_email=approver_email,
                approver_password=approver_password,
                business_date=today.isoformat(),
                member_id=str(member.id),
                trainer_id=trainer_id,
                package_id=str(package.id),
                starts_at=start.isoformat(),
                ends_at=end.isoformat(),
                passwords=[owner_password, approver_password, trainer_password],
            )
        )
    connection.close()
    return states


def _blank(states: Sequence[GymPt]) -> None:
    for state in states:
        state.owner_password = ""
        state.approver_password = ""
        state.passwords.clear()
        state.owner_jar.cookies.clear()
        state.approver_jar.cookies.clear()


def _money_text(value: object) -> str:
    if value is None:
        return "0.00"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(amount.quantize(Decimal("0.01")), "f")


def _count_int(value: object) -> int:
    if type(value) is int:
        return value
    return 0


def _snapshot(gym_id: UUID) -> dict[str, object]:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return _snapshot_in_context(gym_id)


def _visible_ids(model: type[Any], gym_id: UUID, raw_ids: set[UUID]) -> set[UUID]:
    if not raw_ids:
        return set()
    found = model.objects.filter(gym_id=gym_id, id__in=raw_ids).values_list("id", flat=True)
    return set(found)


def _same_gym_links(
    gym_id: UUID,
    columns: Sequence[tuple[Sequence[UUID | None], type[Any], bool]],
) -> list[tuple[str, str | None]]:
    """Build owner/related gym pairs for foreign keys visible under RLS.

    A null optional key is omitted. A null required key, or any key that is
    not visible in this gym, is a missing related gym.
    """

    owner = str(gym_id)
    links: list[tuple[str, str | None]] = []
    for values, model, required in columns:
        present = {item for item in values if item is not None}
        visible = _visible_ids(model, gym_id, present)
        for value in values:
            if value is None:
                if required:
                    links.append((owner, None))
                continue
            links.append((owner, owner if value in visible else None))
    return links


def _column(rows: Sequence[tuple[Any, ...]], index: int) -> list[UUID | None]:
    values: list[UUID | None] = []
    for row in rows:
        value = row[index]
        if value is None or isinstance(value, UUID):
            values.append(value)
        else:
            values.append(None)
    return values


def _snapshot_in_context(gym_id: UUID) -> dict[str, object]:
    sales = PTSale.objects.filter(gym_id=gym_id, status=PTSale.Status.POSTED)
    commissions = PTCommission.objects.filter(gym_id=gym_id, status=PTCommission.Status.PAID)
    payouts = PTCommissionPayment.objects.filter(
        gym_id=gym_id,
        status=PTCommissionPayment.Status.POSTED,
        payment_method="CASH",
    )
    movements = CashTransaction.objects.filter(
        gym_id=gym_id,
        direction=CashTransaction.Direction.OUT,
        source_type=CashTransaction.SourceType.EXPENSE,
    )
    closed = CashSession.objects.filter(gym_id=gym_id, status=CashSession.Status.CLOSED)
    bookings = PTBooking.objects.filter(gym_id=gym_id, status=PTBooking.Status.ATTENDED)
    ledger = ServiceSessionLedger.objects.filter(
        gym_id=gym_id, reason=ServiceSessionLedger.Reason.ATTENDED
    )
    grants = MemberServicePackage.objects.filter(gym_id=gym_id)
    expenses = Expense.objects.filter(
        gym_id=gym_id,
        status=Expense.Status.POSTED,
        category__system_key="PT_COMMISSION",
    )
    sale_rows = list(
        sales.values_list("member_id", "trainer_staff_profile_id", "service_package_id")
    )
    grant_rows = list(grants.values_list("member_id", "service_package_id", "pt_sale_id"))
    booking_rows = list(
        bookings.values_list("trainer_staff_profile_id", "member_id", "member_service_package_id")
    )
    commission_rows = list(commissions.values_list("pt_sale_id", "trainer_staff_profile_id"))
    payout_rows = list(
        payouts.values_list(
            "pt_commission_id",
            "expense_id",
            "cash_session_id",
            "cash_transaction_id",
        )
    )
    movement_rows = list(movements.values_list("cash_session_id", "source_id"))
    closed_rows = list(closed.values_list("opened_by_membership_id"))
    ledger_rows = list(ledger.values_list("member_service_package_id", "pt_booking_id"))
    expense_rows = list(
        expenses.values_list("category_id", "cash_session_id", "cash_transaction_id")
    )
    links = _same_gym_links(
        gym_id,
        (
            (_column(sale_rows, 0), Member, True),
            (_column(sale_rows, 1), StaffProfile, True),
            (_column(sale_rows, 2), ServicePackage, False),
            (_column(grant_rows, 0), Member, True),
            (_column(grant_rows, 1), ServicePackage, True),
            (_column(grant_rows, 2), PTSale, False),
            (_column(booking_rows, 0), StaffProfile, True),
            (_column(booking_rows, 1), Member, True),
            (_column(booking_rows, 2), MemberServicePackage, False),
            (_column(commission_rows, 0), PTSale, True),
            (_column(commission_rows, 1), StaffProfile, True),
            (_column(payout_rows, 0), PTCommission, True),
            (_column(payout_rows, 1), Expense, True),
            (_column(payout_rows, 2), CashSession, True),
            (_column(payout_rows, 3), CashTransaction, True),
            (_column(movement_rows, 0), CashSession, True),
            (_column(movement_rows, 1), Expense, True),
            (_column(closed_rows, 0), GymMembership, True),
            (_column(ledger_rows, 0), MemberServicePackage, True),
            (_column(ledger_rows, 1), PTBooking, True),
            (_column(expense_rows, 0), ExpenseCategory, True),
            (_column(expense_rows, 1), CashSession, True),
            (_column(expense_rows, 2), CashTransaction, True),
        ),
    )
    mismatch = same_gym_link_mismatches(links)
    payout_count = payouts.count()
    return {
        "sales_posted": sales.count(),
        "sale_gross": _money_text(sales.aggregate(total=Sum("gross_fee"))["total"]),
        "commissions_paid": commissions.count(),
        "commission_paid": _money_text(commissions.aggregate(total=Sum("paid_amount"))["total"]),
        "payouts_posted": payout_count,
        "payout_method": "CASH" if payout_count == 1 else "",
        "cash_out_count": movements.count(),
        "cash_out_amount": _money_text(movements.aggregate(total=Sum("amount"))["total"]),
        "cash_sessions_closed": closed.count(),
        "opening_cash": _money_text(closed.aggregate(total=Sum("opening_cash"))["total"]),
        "cash_sales": _money_text(closed.aggregate(total=Sum("cash_sales"))["total"]),
        "cash_expenses": _money_text(closed.aggregate(total=Sum("cash_expenses"))["total"]),
        "manual_net": _money_text(closed.aggregate(total=Sum("manual_net"))["total"]),
        "expected_closing": _money_text(closed.aggregate(total=Sum("expected_closing"))["total"]),
        "actual_closing": _money_text(closed.aggregate(total=Sum("actual_closing"))["total"]),
        "difference": _money_text(closed.aggregate(total=Sum("difference"))["total"]),
        "bookings_attended": bookings.count(),
        "ledger_attended": ledger.count(),
        "grants": grants.count(),
        "granted_sessions": _count_int(grants.aggregate(total=Sum("granted_sessions"))["total"]),
        "expenses_posted": expenses.count(),
        "gym_mismatch": mismatch,
    }


def _observed(states: Sequence[GymPt]) -> list[dict[str, object]] | None:
    rows: list[dict[str, object]] = []
    try:
        for state in states:
            connection.close()
            rows.append(_snapshot(state.gym_id))
        return rows
    except (DatabaseError, OSError, ValueError, PtError):
        return None
    finally:
        connection.close()


class Runner:
    def __init__(self, client: Client, states: list[GymPt]) -> None:
        self.client = client
        self.states = states

    def run_step(self, step: Mapping[str, object]) -> dict[str, object]:
        started = time.perf_counter()
        scenario = str(step["scenario"])
        status, payload, leak, credential = self._perform(step)
        classification = classify_status(scenario, status)
        if classification == "success" and not self._shape_ok(scenario, payload):
            classification = "unexpected"
        elapsed = int((time.perf_counter() - started) * 1000)
        return public_outcome(
            {
                "scenario": scenario,
                "phase": str(step.get("phase", "")),
                "transport": "http",
                "status": status,
                "classification": classification,
                "latency_ms": max(0, elapsed),
                "leak": leak,
                "credential": credential,
            }
        )

    def _state(self, index: object) -> GymPt:
        if type(index) is not int or index not in (0, 1):
            raise PtError("staging pt gym selector is invalid")
        return self.states[index]

    def _other(self, state: GymPt) -> GymPt:
        return self.states[1 - state.index]

    def _shape_ok(self, scenario: str, payload: bytes) -> bool:
        if scenario == "pt_sale_create":
            return _text(payload, ("data", "status")) == "DRAFT" and bool(
                _nested_id(payload, ("data", "id"))
            )
        if scenario == "pt_sale_submit":
            return _text(payload, ("data", "status")) == "PENDING_APPROVAL"
        if scenario in {"pt_sale_approve", "pt_sale_approve_replay"}:
            return (
                _money(payload, ("data", "commission", "commission_amount")) == COMMISSION_AMOUNT
                and _text(payload, ("data", "commission", "status")) == "ACCRUED"
                and bool(_nested_id(payload, ("data", "sale", "member_service_package_id")))
            )
        if scenario == "pt_booking_create":
            return _text(payload, ("data", "status")) == "BOOKED" and bool(
                _nested_id(payload, ("data", "id"))
            )
        if scenario == "pt_booking_attend":
            return _text(payload, ("data", "status")) == "ATTENDED"
        if scenario in {"pt_cash_payout", "pt_cash_payout_replay"}:
            return (
                _text(payload, ("data", "commission_after", "status")) == "PAID"
                and _money(payload, ("data", "commission_after", "paid_amount"))
                == COMMISSION_AMOUNT
                and _money(payload, ("data", "commission_after", "outstanding_amount")) == "0.00"
                and _text(payload, ("data", "payment", "payment_method")) == "CASH"
                and _text(payload, ("data", "cash_transaction", "direction")) == "OUT"
                and _money(payload, ("data", "cash_transaction", "amount")) == COMMISSION_AMOUNT
            )
        if scenario == "cash_expected":
            return (
                _text(payload, ("data", "status")) == "OPEN"
                and _money(payload, ("data", "expected_closing")) == EXPECTED_CLOSING
                and _money(payload, ("data", "cash_expenses")) == COMMISSION_AMOUNT
            )
        if scenario in {"cash_close", "cash_close_replay"}:
            return (
                _text(payload, ("data", "session", "status")) == "CLOSED"
                and _money(payload, ("data", "session", "expected_closing")) == EXPECTED_CLOSING
                and _money(payload, ("data", "session", "actual_closing")) == EXPECTED_CLOSING
                and _money(payload, ("data", "session", "difference")) == DIFFERENCE
            )
        if scenario == "pt_report":
            return _count_field(payload, ("data", "totals", "attended_sessions")) == 1
        return True

    def _perform(self, step: Mapping[str, object]) -> tuple[object, bytes, bool, bool]:
        scenario = str(step["scenario"])
        state = self._state(step.get("gym"))
        other = self._other(state)
        if scenario == "auth_login_owner":
            return self._login(scenario, state.owner_jar, state.owner_email, state.owner_password)
        if scenario == "auth_login_approver":
            return self._login(
                scenario, state.approver_jar, state.approver_email, state.approver_password
            )
        if scenario == "pt_sale_create":
            return self._sale_create(state, other)
        if scenario == "pt_sale_submit":
            return self._sale_action(scenario, state, other, "submit", state.owner_jar, "submit")
        if scenario == "pt_sale_self_approve":
            return self._sale_action(scenario, state, other, "approve", state.owner_jar, "self")
        if scenario in {"pt_sale_approve", "pt_sale_approve_replay"}:
            return self._sale_action(
                scenario, state, other, "approve", state.approver_jar, "approve"
            )
        if scenario == "pt_booking_create":
            return self._booking_create(state, other)
        if scenario == "pt_booking_attend":
            return self._attend(scenario, state, other, state.booking_id, state.owner_jar, "attend")
        if scenario == "cash_open":
            return self._cash_open(state, other)
        if scenario == "pt_payout_self":
            return self._payout(scenario, state, other, state.approver_jar, "payself")
        if scenario in {"pt_cash_payout", "pt_cash_payout_replay"}:
            return self._payout(scenario, state, other, state.owner_jar, "payout")
        if scenario == "cash_expected":
            return self._cash_read(scenario, state, other, state.cash_id)
        if scenario in {"cash_close", "cash_close_replay", "cash_close_immutable"}:
            return self._cash_close(scenario, state, other, state.cash_id)
        if scenario == "pt_report":
            path = (
                "/api/operations/reports/pt-sessions"
                f"?date_from={state.business_date}&date_to={state.business_date}"
            )
            return self._call(
                "pt_report", "GET", path, jar=state.owner_jar, state=state, other=other
            )
        if scenario == "cross_pt_sale":
            return self._sale_action(
                scenario, state, other, "approve", state.owner_jar, "xsale", sale_id=other.sale_id
            )
        if scenario == "cross_pt_attend":
            return self._attend(
                scenario, state, other, other.booking_id, state.owner_jar, "xattend"
            )
        if scenario == "cross_pt_payout":
            return self._payout(
                scenario,
                state,
                other,
                state.owner_jar,
                "xpayout",
                commission_id=other.commission_id,
            )
        if scenario == "cross_cash_read":
            return self._cash_read(scenario, state, other, other.cash_id)
        if scenario == "cross_cash_close":
            return self._cash_close(scenario, state, other, other.cash_id)
        return "unavailable", b"", False, False

    def _login(
        self, scenario: str, jar: Jar, email: str, password: str
    ) -> tuple[object, bytes, bool, bool]:
        state = self.states[0]
        other = self.states[1]
        for item in self.states:
            if jar is item.owner_jar or jar is item.approver_jar:
                state = item
                other = self._other(item)
                break
        if len(jar.cookies.get("gms_csrf", "")) < 32:
            assert_pt_cash_path("/api/auth/csrf")
            _refresh_csrf(self.client, jar)
        status, payload, leak, credential = self._call(
            scenario,
            "POST",
            "/api/auth/login",
            jar=jar,
            body=_json_body({"email": email, "password": password, "device_name": "pt-client"}),
            csrf=True,
            state=state,
            other=other,
            extra_markers=(password,),
        )
        if not _login_ok(status, payload, jar.cookies):
            status = status if status != 200 else "login_rejected"
        return status, payload, leak, credential

    def _sale_create(self, state: GymPt, other: GymPt) -> tuple[object, bytes, bool, bool]:
        body = _json_body(
            {
                "member_id": state.member_id,
                "trainer_staff_profile_id": state.trainer_id,
                "service_package_id": state.package_id,
                "service_date": state.business_date,
                "description": "PT session pack",
                "gross_fee": GROSS_FEE,
                "currency": "THB",
            }
        )
        status, payload, leak, credential = self._call(
            "pt_sale_create",
            "POST",
            "/api/operations/pt-sales",
            jar=state.owner_jar,
            body=body,
            csrf=True,
            idempotency=make_key("sale", state.index, 0),
            state=state,
            other=other,
        )
        if status == 201:
            state.sale_id = _nested_id(payload, ("data", "id"))
        return status, payload, leak, credential

    def _sale_action(
        self,
        scenario: str,
        state: GymPt,
        other: GymPt,
        action: str,
        jar: Jar,
        kind: str,
        sale_id: str | None = None,
    ) -> tuple[object, bytes, bool, bool]:
        target = state.sale_id if sale_id is None else sale_id
        if not target:
            return "unavailable", b"", False, False
        status, payload, leak, credential = self._call(
            scenario,
            "POST",
            f"/api/operations/pt-sales/{target}/{action}",
            jar=jar,
            body=b"{}",
            csrf=True,
            idempotency=make_key(kind, state.index, 0),
            state=state,
            other=other,
        )
        if scenario == "pt_sale_approve" and status == 200:
            state.commission_id = _nested_id(payload, ("data", "commission", "id"))
            state.grant_id = _nested_id(payload, ("data", "sale", "member_service_package_id"))
        return status, payload, leak, credential

    def _booking_create(self, state: GymPt, other: GymPt) -> tuple[object, bytes, bool, bool]:
        if not state.grant_id:
            return "unavailable", b"", False, False
        body = _json_body(
            {
                "trainer_staff_profile_id": state.trainer_id,
                "member_id": state.member_id,
                "member_service_package_id": state.grant_id,
                "starts_at": state.starts_at,
                "ends_at": state.ends_at,
                "unmetered": False,
            }
        )
        status, payload, leak, credential = self._call(
            "pt_booking_create",
            "POST",
            "/api/operations/pt-bookings",
            jar=state.owner_jar,
            body=body,
            csrf=True,
            idempotency=make_key("book", state.index, 0),
            state=state,
            other=other,
        )
        if status == 201:
            state.booking_id = _nested_id(payload, ("data", "id"))
        return status, payload, leak, credential

    def _attend(
        self,
        scenario: str,
        state: GymPt,
        other: GymPt,
        booking_id: str,
        jar: Jar,
        kind: str,
    ) -> tuple[object, bytes, bool, bool]:
        if not booking_id:
            return "unavailable", b"", False, False
        return self._call(
            scenario,
            "POST",
            f"/api/operations/pt-bookings/{booking_id}/attend",
            jar=jar,
            body=b"{}",
            csrf=True,
            idempotency=make_key(kind, state.index, 0),
            state=state,
            other=other,
        )

    def _payout(
        self,
        scenario: str,
        state: GymPt,
        other: GymPt,
        jar: Jar,
        kind: str,
        commission_id: str | None = None,
    ) -> tuple[object, bytes, bool, bool]:
        target = state.commission_id if commission_id is None else commission_id
        if not target:
            return "unavailable", b"", False, False
        if not state.cash_id and commission_id is None:
            return "unavailable", b"", False, False
        body = _json_body(
            {
                "amount": COMMISSION_AMOUNT,
                "business_date": state.business_date,
                "payment_method": "CASH",
                "cash_session_id": state.cash_id,
                "reference_number": "",
                "policy_note": "",
            }
        )
        return self._call(
            scenario,
            "POST",
            f"/api/operations/pt-commissions/{target}/record-payment",
            jar=jar,
            body=body,
            csrf=True,
            idempotency=make_key(kind, state.index, 0),
            state=state,
            other=other,
        )

    def _cash_open(self, state: GymPt, other: GymPt) -> tuple[object, bytes, bool, bool]:
        body = _json_body(
            {
                "register_code": REGISTER_CODE,
                "business_date": state.business_date,
                "opening_cash": OPENING_CASH,
            }
        )
        status, payload, leak, credential = self._call(
            "cash_open",
            "POST",
            "/api/operations/cash-sessions/open",
            jar=state.owner_jar,
            body=body,
            csrf=True,
            idempotency=make_key("cashopen", state.index, 0),
            state=state,
            other=other,
        )
        if status == 201:
            state.cash_id = _nested_id(payload, ("data", "session", "id"))
        return status, payload, leak, credential

    def _cash_read(
        self, scenario: str, state: GymPt, other: GymPt, session_id: str
    ) -> tuple[object, bytes, bool, bool]:
        if not session_id:
            return "unavailable", b"", False, False
        return self._call(
            scenario,
            "GET",
            f"/api/operations/cash-sessions/{session_id}",
            jar=state.owner_jar,
            state=state,
            other=other,
        )

    def _cash_close(
        self, scenario: str, state: GymPt, other: GymPt, session_id: str
    ) -> tuple[object, bytes, bool, bool]:
        if not session_id:
            return "unavailable", b"", False, False
        if scenario == "cash_close_immutable":
            kind = "close2"
        elif scenario == "cross_cash_close":
            kind = "xclose"
        else:
            kind = "close"
        body = _json_body({"actual_closing": EXPECTED_CLOSING, "difference_reason": ""})
        return self._call(
            scenario,
            "POST",
            f"/api/operations/cash-sessions/{session_id}/reconcile",
            jar=state.owner_jar,
            body=body,
            csrf=True,
            idempotency=make_key(kind, state.index, 0),
            state=state,
            other=other,
        )

    def _call(
        self,
        scenario: str,
        method: str,
        path: str,
        *,
        jar: Jar,
        state: GymPt,
        other: GymPt,
        body: bytes | None = None,
        csrf: bool = False,
        idempotency: str | None = None,
        extra_markers: Sequence[str] = (),
    ) -> tuple[object, bytes, bool, bool]:
        assert_pt_cash_path(path)
        status, payload, cookies = self.client.exchange(
            method,
            path,
            jar=jar,
            body=body,
            csrf=csrf,
            idempotency=idempotency,
        )
        if cookies and scenario in {"auth_login_owner", "auth_login_approver"}:
            jar.cookies.update(cookies)
        markers = [
            item.encode("utf-8") for item in (*state.passwords, *extra_markers) if len(item) >= 12
        ]
        text = payload.decode("utf-8", "replace")
        credential = marker_present(payload, markers) or _JWT.search(text) is not None
        other_markers = [
            item
            for item in (
                other.gym_code,
                other.sale_id,
                other.booking_id,
                other.commission_id,
                other.grant_id,
                other.cash_id,
                other.member_id,
                other.trainer_id,
            )
            if len(item) >= 8
        ]
        leak = status in {200, 201} and marker_present(
            payload, [item.encode("utf-8") for item in other_markers]
        )
        return status, payload, leak, credential


def _base_report(profile: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": "local-disposable-pt-cash-characterization",
        "profile": profile,
        "local_pt_cash_status": "not-run",
        "local_isolation_status": "not-run",
        "local_financial_reconciliation_status": "not-run",
        "local_soak_status": "not-run",
        "thresholds_are_approved_slos": False,
        "failure_stage": "",
        "failure_detail": "",
        "cross_gym_leakage": 0,
        "credential_exposure": 0,
        "unexpected_errors": 0,
        "api_unexpected_5xx": 0,
        "caddy_metrics_observed": False,
        "caddy_5xx_delta": None,
        "reconciliation_matched": False,
        "data_corruption": False,
        "postgres_connection_peak": None,
        "postgres_max_connections": None,
        "cancelled_operations": 0,
        "required_complete": False,
        "http_p95_ms": None,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "evidence_name": "pt-cash.json",
        "transport_note": (
            "Browser login, till open, PT sale, booking attendance, CASH "
            "commission payout, expected closing, till reconcile, and the PT "
            "session report are HTTP through Caddy and the BFF. Gym, owner, "
            "manager, trainer, member, package, assignment, and ORM "
            "reconciliation are service-only."
        ),
        "architecture_note": (
            "Local arm64 or amd64-emulated ClamAV on a 4 GiB Docker VM cannot "
            "validate the 8 GiB production planning budget. These ceilings are "
            "not an approved SLO. No soak profile is defined."
        ),
        "caddy_note": (
            "caddy_metrics_observed is false and caddy_5xx_delta is null when "
            "the private metrics endpoint does not answer. That is unavailable, "
            "not zero. A private Prometheus document with no 5xx series is an "
            "observed zero."
        ),
    }
    for flag in PROOF_FLAGS:
        report[flag] = False
    return report


def _cross_ok(
    plan: Sequence[Mapping[str, object]], outcomes: Sequence[Mapping[str, object]]
) -> bool:
    names = (
        "cross_pt_sale",
        "cross_pt_attend",
        "cross_pt_payout",
        "cross_cash_read",
        "cross_cash_close",
    )
    for name in names:
        planned = sum(1 for step in plan if step.get("scenario") == name)
        seen = sum(
            1
            for item in outcomes
            if item.get("scenario") == name and item.get("classification") == "expected_rejection"
        )
        if seen != planned:
            return False
    return True


def _finish(
    profile: str,
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    *,
    started: float,
    postgres_peak: int | None,
    postgres_max: int | None,
    caddy_before: int | None,
    caddy_after: int | None,
    observed: list[dict[str, object]] | None,
    cancelled: int,
    required_complete: bool,
    stage: str,
    detail: str,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in outcomes:
        grouped[str(item.get("scenario"))].append(dict(item))
    scenarios = [summarize_scenario(name, grouped[name]) for name in sorted(grouped)]
    latencies = [
        latency
        for item in outcomes
        if item.get("transport") == "http"
        and item.get("classification") == "success"
        and isinstance((latency := item.get("latency_ms")), int)
        and not isinstance(latency, bool)
    ]
    financial_status, matched, corruption = assess_pt_cash(plan, outcomes, observed)
    if caddy_before is None or caddy_after is None:
        caddy_observed = False
        caddy_delta = None
    else:
        caddy_observed = True
        caddy_delta = caddy_after - caddy_before
    leakage = sum(1 for item in outcomes if item.get("leak") is True)
    report = _base_report(profile)
    report.update(
        {
            "failure_stage": stage,
            "failure_detail": detail,
            "scenarios": scenarios,
            "cross_gym_leakage": leakage,
            "credential_exposure": sum(1 for item in outcomes if item.get("credential") is True),
            "unexpected_errors": sum(
                1 for item in outcomes if item.get("classification") == "unexpected"
            ),
            "api_unexpected_5xx": sum(
                1
                for item in outcomes
                if isinstance((status := item.get("status")), int)
                and not isinstance(status, bool)
                and status >= 500
            ),
            "caddy_metrics_observed": caddy_observed,
            "caddy_5xx_delta": caddy_delta,
            "reconciliation_matched": matched,
            "data_corruption": corruption,
            "postgres_connection_peak": postgres_peak,
            "postgres_max_connections": postgres_max,
            "cancelled_operations": cancelled,
            "required_complete": required_complete,
            "http_p95_ms": percentile_ms(latencies, 95),
            "wall_time_seconds": round(max(0.0, time.perf_counter() - started), 3),
            "peak_active_workers": 1 if outcomes else 0,
            "local_isolation_status": "pass"
            if leakage == 0 and _cross_ok(plan, outcomes)
            else "fail",
            "local_financial_reconciliation_status": financial_status,
            "local_soak_status": "not-run",
            "reconciliation": {
                "transport": "service-only",
                "matched": matched,
                "gyms": observed or [],
            },
        }
    )
    report["local_pt_cash_status"] = financial_status
    status, reasons = judge(report)
    report["local_pt_cash_status"] = status
    report["gate_reasons"] = reasons
    report["connection_ceiling"] = "fail" if "connection_ceiling" in reasons else "pass"
    return report


def _metrics_observation() -> tuple[bool, int | None]:
    try:
        ips = _resolve("caddy", 2020)
        assert_metrics_target("caddy", 2020, ips)
        conn = http.client.HTTPConnection(ips[0], 2020, timeout=3)
        try:
            conn.request("GET", "/metrics", headers={"Host": "caddy", "Connection": "close"})
            response = conn.getresponse()
            if response.status != 200:
                return False, None
            text = response.read(1_000_000).decode("utf-8", "replace")
        finally:
            conn.close()
    except (OSError, LoadError, ValueError):
        return False, None
    return caddy_metrics_observation(text)


def _sample_caddy() -> int | None:
    observed, total = _metrics_observation()
    if not observed or type(total) is not int:
        return None
    return total


def run_characterization(profile_name: str, cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise PtError("staging pt characterization requires the staging rehearsal settings")
    if os.environ.get("STAGING_PT_CASH_PROJECT") != _REQUIRED_PROJECT:
        raise PtError("staging pt characterization requires the disposable project")
    for flag in _REFUSED_LIVE_PROOF:
        if os.environ.get(flag) == "1":
            raise PtError("staging pt characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_PT_CASH_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"]["HOST"]),
        database_user=str(settings.DATABASES["default"]["USER"]),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise PtError("staging pt characterization requires the rehearsal flag")
    profile = resolve_profile(profile_name)
    if os.environ.get("STAGING_PT_CASH_PROFILE") != profile.name:
        raise PtError("staging pt profile was not the fixed rehearsal profile")
    plan = build_plan()
    started = time.perf_counter()
    deadline = time.monotonic() + profile.duration_seconds
    states = _seed()
    sensitive = [
        item
        for state in states
        for item in (
            *state.passwords,
            state.owner_email,
            state.approver_email,
            state.gym_code,
            state.member_id,
            state.trainer_id,
            state.package_id,
            "PT Cash A",
            "PT Cash B",
        )
    ]
    client = Client(cafile, profile.timeout_seconds)
    runner = Runner(client, states)
    outcomes: list[dict[str, object]] = []
    cancelled = 0
    fatal = False
    caddy_before = _sample_caddy()
    samples: list[tuple[int, int]] = []
    stop = threading.Event()

    def sample() -> None:
        try:
            while not stop.is_set():
                samples.append(_sample_postgres())
                stop.wait(0.5)
        except (DatabaseError, OSError, PtError, LoadError, ValueError):
            samples.append((-1, -1))
        finally:
            connection.close()

    sampler = threading.Thread(target=sample, name="pt-postgres")
    sampler.start()
    try:
        for step in plan:
            if time.monotonic() >= deadline:
                cancelled += 1
                fatal = True
                break
            outcome = runner.run_step(step)
            outcomes.append(outcome)
            scenario = step.get("scenario")
            if scenario in {"auth_login_owner", "auth_login_approver"} and (
                outcome["classification"] != "success"
            ):
                fatal = True
                break
            if outcome["classification"] == "unexpected":
                fatal = True
                break
        caddy_after = _sample_caddy()
        stop.set()
        sampler.join(5)
        postgres_peak, postgres_max = _postgres_peak(samples)
        for state in states:
            sensitive.extend(
                (
                    state.sale_id,
                    state.booking_id,
                    state.commission_id,
                    state.grant_id,
                    state.cash_id,
                    str(state.gym_id),
                )
            )
        report = _finish(
            profile.name,
            plan,
            outcomes,
            started=started,
            postgres_peak=postgres_peak,
            postgres_max=postgres_max,
            caddy_before=caddy_before,
            caddy_after=caddy_after,
            observed=_observed(states),
            cancelled=cancelled,
            required_complete=(cancelled == 0 and not fatal and scenarios_match(plan, outcomes)),
            stage="http",
            detail="",
        )
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if material_leaked(encoded, sensitive):
            raise PtError("staging pt report failed redaction")
        return report
    finally:
        stop.set()
        sampler.join(5)
        _blank(states)


def characterize(profile_name: str, cafile: Path) -> dict[str, Any]:
    try:
        return run_characterization(profile_name, cafile)
    except Exception as exc:
        report = failure_document(PROFILE_NAME, "characterization")
        report["failure_detail"] = _safe_text(exc)
        report["local_pt_cash_status"] = "fail"
        report["gate_reasons"] = ["characterization"]
        return report


class Command(BaseCommand):
    help = "Run the fixed staging cash commission payout characterization. Not a production proof."

    def handle(self, *args: object, **options: object) -> None:
        profile = os.environ.get("STAGING_PT_CASH_PROFILE", "")
        cafile = Path(os.environ.get("STAGING_PT_CASH_CA_FILE", ""))
        report = characterize(profile, cafile)
        self.stdout.write(
            json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
        if report.get("local_pt_cash_status") != "pass":
            raise CommandError("local pt-cash characterization failed")

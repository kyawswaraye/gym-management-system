"""Bounded local tenant-isolation characterization for the disposable rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and runs a
fixed BFF plus runtime PostgreSQL matrix. It prints JSON only. It does not
prove tenant isolation, a penetration test, a formal security review, or
production security. tenant_isolation_proven stays false.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.db.models import Sum

from apps.audit.models import AuditLog
from apps.cashbook.models import CashSession
from apps.cashbook.services import open_cash_session
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.common.isolation_characterization import (
    DURATION_SECONDS,
    LOCAL_ISOLATION_FLAGS,
    PROFILE_NAME,
    PUBLIC_ORIGIN,
    SEED_COUNTS,
    TIMEOUT_SECONDS,
    assemble,
    assert_isolation_route,
    build_plan,
    failure_document,
    metrics_delta,
    public_outcome,
    resolve_profile,
    runtime_status,
)
from apps.common.load_characterization import (
    MAX_RESPONSE_BYTES,
    PROOF_FLAGS,
    assert_metrics_target,
    assert_rehearsal_context,
    caddy_metrics_observation,
    clip_body,
    material_leaked,
)
from apps.common.security_characterization import (
    auth_cookie_flags,
    credential_in_body,
    marker_in_success,
    redirect_accepted,
    required_auth_cookies,
)
from apps.expenses.models import Expense
from apps.expenses.services import create_expense_draft, submit_expense
from apps.files.models import FileAsset
from apps.members.checkin_services import create_manual_check_in
from apps.members.models import CheckIn, Member
from apps.members.services import create_member, create_membership, create_plan, gym_today
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    _json_body,
    _login_ok,
    _refresh_csrf,
    _resolve,
)
from apps.tenants.management.commands.staging_pt_characterization import (
    _install_owner,
    _install_staff,
)
from apps.tenants.models import ExpenseCategory, GymMembership, GymSettings
from apps.tenants.services import provision_gym
from apps.trainers.models import PTSale, ServicePackage
from apps.trainers.pt_sale_services import create_pt_sale, submit_pt_sale
from apps.trainers.services import create_assignment, create_service_package

_REQUIRED_SETTINGS = "config.settings.staging_rehearsal"
_REQUIRED_PROJECT = "gym-staging-rehearsal"
_REFUSED_CLAIMS = (
    "penetration_test_proven",
    "formal_review_proven",
    "production_security_proven",
    "tenant_isolation_proven",
    "load_test_proven",
)
_GYMS = (
    ("ISOGYMA", "Isolation Gym A", "owner-iso-a@rehearsal.invalid"),
    ("ISOGYMB", "Isolation Gym B", "owner-iso-b@rehearsal.invalid"),
)


class IsolationError(CommandError):
    """The isolation characterization could not finish a fixed step."""


def _safe_text(exc: BaseException) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "@" in detail or "password" in lowered or "token" in lowered or "secret" in lowered:
        return type(exc).__name__
    if len(detail) > 180:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}"


def _cents(value: object) -> int:
    if value is None:
        return 0
    return int(Decimal(str(value)) * 100)


@dataclass
class GymIsolation:
    index: int
    gym_id: UUID
    gym_code: str
    owner_email: str
    owner_password: str
    member_id: str
    expense_id: str
    cash_id: str
    sale_id: str
    file_id: str
    business_date: str
    owner_jar: Jar = field(default_factory=Jar)
    secrets: list[str] = field(default_factory=list)

    def blank(self) -> None:
        self.owner_password = ""
        self.secrets.clear()
        self.owner_jar.cookies.clear()


def _seed() -> list[GymIsolation]:
    import secrets

    states: list[GymIsolation] = []
    for index, (code, name, owner_email) in enumerate(_GYMS):
        owner_password = secrets.token_urlsafe(24)
        trainer_password = secrets.token_urlsafe(24)
        suffix = code[-1].lower()
        trainer_email = f"trainer-iso-{suffix}@rehearsal.invalid"
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=owner_email)
            owner = _install_owner(provisioned.owner_membership, owner_email, owner_password)
            trainer_profile = _install_staff(
                owner,
                email=trainer_email,
                password=trainer_password,
                role_key="TRAINER",
                staff_code=f"ISOTR{code[-1]}",
                first_name="Isolation",
                last_name="Trainer",
                display_name="Isolation Trainer",
                title="Trainer",
                commission=Decimal("0"),
            )
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                today = gym_today(provisioned.gym)
                category = ExpenseCategory.objects.get(gym=provisioned.gym, code="RENT")
                plan_row = create_plan(
                    gym=provisioned.gym,
                    actor=owner,
                    code="ISO30",
                    name="Isolation Plan",
                    description="",
                    duration_value=30,
                    duration_unit="DAY",
                    price=Decimal("0.00"),
                )
                member = create_member(
                    gym=provisioned.gym,
                    actor=owner,
                    member_number="",
                    first_name="Isolation",
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
                create_membership(
                    gym=provisioned.gym,
                    actor=owner,
                    member=member,
                    plan=plan_row,
                    start_date=today,
                    discount_amount=Decimal("0.00"),
                    note="",
                )
                create_service_package(
                    gym=provisioned.gym,
                    actor=owner,
                    package_type=ServicePackage.PackageType.PT,
                    code="ISO10",
                    name="Isolation PT",
                    session_count=1,
                    validity_value=30,
                    validity_unit=ServicePackage.ValidityUnit.DAY,
                    price=Decimal("0.00"),
                    request_id="isolation-seed",
                    ip_address=None,
                    user_agent="isolation",
                )
                create_assignment(
                    gym=provisioned.gym,
                    actor=owner,
                    trainer_staff_profile_id=UUID(trainer_profile),
                    member_id=member.id,
                    effective_from=today,
                    effective_until=None,
                    reason="Isolation characterization",
                    request_id="isolation-seed",
                    ip_address=None,
                    user_agent="isolation",
                )
                expense = create_expense_draft(
                    gym=provisioned.gym,
                    actor=owner,
                    fields={
                        "category_id": category.id,
                        "business_date": today,
                        "description": "Isolation draft",
                        "amount": Decimal("1.00"),
                        "currency": "THB",
                        "payment_method": "BANK_TRANSFER",
                    },
                    request_id="isolation-seed",
                    ip_address=None,
                    user_agent="isolation",
                )
                submit_expense(
                    gym=provisioned.gym,
                    actor=owner,
                    expense_id=expense.id,
                    request_id="isolation-seed-submit",
                    ip_address=None,
                    user_agent="isolation",
                )
                session = open_cash_session(
                    gym=provisioned.gym,
                    actor=owner,
                    register_code=f"ISO{code[-1]}1",
                    business_date=today,
                    opening_cash=Decimal("0.00"),
                    request_id="isolation-seed-cash",
                    ip_address=None,
                    user_agent="isolation",
                )
                check_in = create_manual_check_in(
                    gym=provisioned.gym,
                    actor=owner,
                    member_id=member.id,
                    occurred_at=None,
                    note="Isolation visit",
                    idempotency_key=f"isolation-checkin-{index:02d}",
                    request_id="isolation-seed-checkin",
                    ip_address=None,
                    user_agent="isolation",
                )
                sale = create_pt_sale(
                    gym=provisioned.gym,
                    actor=owner,
                    member_id=member.id,
                    trainer_staff_profile_id=UUID(trainer_profile),
                    service_date=today,
                    description="Isolation session",
                    gross_fee=Decimal("1.00"),
                    currency="THB",
                    payment=None,
                    service_package_id=None,
                    request_id="isolation-seed-pt",
                    ip_address=None,
                    user_agent="isolation",
                )
                submit_pt_sale(
                    gym=provisioned.gym,
                    actor=owner,
                    sale_id=sale.id,
                    request_id="isolation-seed-pt-submit",
                    ip_address=None,
                    user_agent="isolation",
                )
                asset = FileAsset.objects.create(
                    gym=provisioned.gym,
                    object_key=f"{provisioned.gym.id}/{uuid4()}",
                    original_name="note.txt",
                    content_type="text/plain",
                    byte_size=4,
                    sha256="ab" * 32,
                    purpose="DOCUMENT",
                    uploaded_by_membership_id=owner.id,
                )
                clear_gym_context()
        except Exception as exc:
            raise IsolationError(f"staging isolation seed failed: {_safe_text(exc)}") from None
        del trainer_password
        states.append(
            GymIsolation(
                index=index,
                gym_id=provisioned.gym.id,
                gym_code=code,
                owner_email=owner_email,
                owner_password=owner_password,
                member_id=str(member.id),
                expense_id=str(expense.id),
                cash_id=str(session.id),
                sale_id=str(sale.id),
                file_id=str(asset.id),
                business_date=today.isoformat(),
                secrets=[owner_password],
            )
        )
        del check_in
    connection.close()
    return states


def _snapshot(states: Sequence[GymIsolation]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for state in states:
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            member = Member.objects.get(id=state.member_id)
            owner = GymMembership.objects.get(gym_id=state.gym_id, is_owner=True)
            expenses = Expense.objects.filter(gym_id=state.gym_id)
            sales = PTSale.objects.filter(gym_id=state.gym_id)
            mismatch = (
                Expense.objects.exclude(gym_id=state.gym_id).count()
                + CheckIn.objects.exclude(gym_id=state.gym_id).count()
                + CashSession.objects.exclude(gym_id=state.gym_id).count()
                + PTSale.objects.exclude(gym_id=state.gym_id).count()
                + Member.objects.exclude(gym_id=state.gym_id).count()
                + FileAsset.objects.exclude(gym_id=state.gym_id).count()
                + AuditLog.objects.exclude(gym_id=state.gym_id).count()
                + GymSettings.objects.exclude(gym_id=state.gym_id).count()
            )
            other = states[1 - state.index]
            rows.append(
                {
                    "gym": state.index,
                    "members": Member.objects.filter(gym_id=state.gym_id).count(),
                    "expenses": expenses.count(),
                    "expenses_pending": expenses.filter(
                        status=Expense.Status.PENDING_APPROVAL
                    ).count(),
                    "expenses_approved": expenses.exclude(approved_by_membership_id=None).count(),
                    "checkins": CheckIn.objects.filter(gym_id=state.gym_id).count(),
                    "cash_sessions": CashSession.objects.filter(gym_id=state.gym_id).count(),
                    "cash_open": CashSession.objects.filter(
                        gym_id=state.gym_id, status=CashSession.Status.OPEN
                    ).count(),
                    "pt_sales": sales.count(),
                    "pt_sales_pending": sales.filter(status=PTSale.Status.PENDING_APPROVAL).count(),
                    "pt_sales_posted": sales.filter(status=PTSale.Status.POSTED).count(),
                    "files": FileAsset.objects.filter(gym_id=state.gym_id).count(),
                    "gym_settings": GymSettings.objects.filter(gym_id=state.gym_id).count(),
                    "audit_rows": AuditLog.objects.filter(gym_id=state.gym_id).count(),
                    "gym_mismatch": mismatch,
                    "expense_sum_cross_cents": _cents(
                        Expense.objects.filter(gym_id=other.gym_id).aggregate(total=Sum("amount"))[
                            "total"
                        ]
                    ),
                    "file_cross": FileAsset.objects.filter(gym_id=other.gym_id).count(),
                    "audit_cross": AuditLog.objects.filter(gym_id=other.gym_id).count(),
                    "member_status": member.status,
                    "member_notes_blank": member.notes == "",
                    "member_gym_match": member.gym_id == state.gym_id,
                    "owner_is_owner": owner.is_owner is True,
                }
            )
            clear_gym_context()
    return rows


def _ddl_denied(sql: str) -> bool:
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql)
            raise RuntimeError("rollback")
    except RuntimeError:
        return False
    except DatabaseError:
        return True


def _missing_context_read() -> bool:
    with transaction.atomic():
        clear_gym_context()
        total = (
            Member.objects.count()
            + Expense.objects.count()
            + FileAsset.objects.count()
            + AuditLog.objects.count()
            + GymSettings.objects.count()
            + PTSale.objects.count()
            + CashSession.objects.count()
            + CheckIn.objects.count()
        )
    return total == 0


def _missing_context_write() -> bool:
    try:
        with transaction.atomic():
            clear_gym_context()
            updated = GymSettings.objects.update(week_starts_on=3)
            if updated != 0:
                raise RuntimeError("rollback")
        return True
    except RuntimeError:
        return False


def _rollback_context(states: Sequence[GymIsolation]) -> bool:
    first, second = states[0], states[1]
    with transaction.atomic():
        set_gym_context(gym_id=first.gym_id)
        own = list(GymSettings.objects.values_list("gym_id", flat=True))
        try:
            with transaction.atomic():
                set_gym_context(gym_id=second.gym_id)
                inner = GymSettings.objects.filter(gym_id=second.gym_id).count()
                if inner != 1:
                    raise IsolationError("inner context")
                raise RuntimeError("rollback")
        except IsolationError:
            return False
        except RuntimeError:
            pass
        after = list(GymSettings.objects.values_list("gym_id", flat=True))
        clear_gym_context()
    return own == [first.gym_id] and after == [first.gym_id]


def _context_reuse(states: Sequence[GymIsolation]) -> bool:
    first = states[0]
    with transaction.atomic():
        set_gym_context(gym_id=first.gym_id)
        visible = GymSettings.objects.filter(gym_id=first.gym_id).count()
        if visible != 1:
            return False
    with transaction.atomic():
        leaked = GymSettings.objects.count() + Member.objects.count() + Expense.objects.count()
    return leaked == 0


def _rls_own(state: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        counts = {
            "members": Member.objects.filter(gym_id=state.gym_id).count(),
            "expenses": Expense.objects.filter(gym_id=state.gym_id).count(),
            "checkins": CheckIn.objects.filter(gym_id=state.gym_id).count(),
            "cash_sessions": CashSession.objects.filter(gym_id=state.gym_id).count(),
            "pt_sales": PTSale.objects.filter(gym_id=state.gym_id).count(),
            "files": FileAsset.objects.filter(gym_id=state.gym_id).count(),
            "gym_settings": GymSettings.objects.filter(gym_id=state.gym_id).count(),
        }
        clear_gym_context()
    return all(counts[key] == SEED_COUNTS[key] for key in counts)


def _rls_cross_read(state: GymIsolation, other: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        cross = (
            Member.objects.filter(gym_id=other.gym_id).count()
            + Expense.objects.filter(gym_id=other.gym_id).count()
            + CheckIn.objects.filter(gym_id=other.gym_id).count()
            + CashSession.objects.filter(gym_id=other.gym_id).count()
            + PTSale.objects.filter(gym_id=other.gym_id).count()
            + FileAsset.objects.filter(gym_id=other.gym_id).count()
            + AuditLog.objects.filter(gym_id=other.gym_id).count()
            + GymSettings.objects.filter(gym_id=other.gym_id).count()
        )
        clear_gym_context()
    return cross == 0


def _rls_cross_update(state: GymIsolation, other: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        updated = Expense.objects.filter(gym_id=other.gym_id).update(description="cross-gym")
        clear_gym_context()
    return updated == 0


def _rls_file_cross(state: GymIsolation, other: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        visible = FileAsset.objects.filter(id=other.file_id).count()
        updated = FileAsset.objects.filter(id=other.file_id).update(original_name="leaked.txt")
        clear_gym_context()
    return visible == 0 and updated == 0


def _rls_audit_cross(state: GymIsolation, other: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        cross = AuditLog.objects.filter(gym_id=other.gym_id).count()
        clear_gym_context()
    return cross == 0


def _rls_aggregate(state: GymIsolation, other: GymIsolation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        own = _cents(Expense.objects.aggregate(total=Sum("amount"))["total"])
        cross = _cents(
            Expense.objects.filter(gym_id=other.gym_id).aggregate(total=Sum("amount"))["total"]
        )
        clear_gym_context()
    return own == 100 and cross == 0


class Probe:
    def __init__(self, client: Client, states: Sequence[GymIsolation]) -> None:
        self.client = client
        self.states = list(states)

    def run(self, scenario: str, gym: int | None) -> dict[str, object]:
        if gym is None:
            return self._runtime(scenario)
        state = self.states[gym]
        other = self.states[1 - gym]
        markers = (other.gym_code.encode(), other.member_id.encode())
        if scenario.startswith("rls_"):
            return self._rls(scenario, state, other)
        if scenario == "auth_login":
            return self._login(state, markers)
        if scenario == "members_list":
            return self._exchange(scenario, state, "GET", "/api/operations/members", markers)
        if scenario == "expenses_list":
            return self._exchange(scenario, state, "GET", "/api/operations/expenses", markers)
        if scenario == "pt_sales_list":
            return self._exchange(scenario, state, "GET", "/api/operations/pt-sales", markers)
        if scenario == "cash_list":
            return self._exchange(scenario, state, "GET", "/api/operations/cash-sessions", markers)
        if scenario == "audit_list":
            return self._exchange(
                scenario,
                state,
                "GET",
                "/api/operations/audit-logs?module=cashbook&limit=25",
                markers,
            )
        if scenario == "report_daily":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/reports/daily?date={state.business_date}",
                markers,
            )
        if scenario == "idor_member_read":
            return self._exchange(
                scenario, state, "GET", f"/api/operations/members/{other.member_id}", markers
            )
        if scenario == "idor_expense_read":
            return self._exchange(
                scenario, state, "GET", f"/api/operations/expenses/{other.expense_id}", markers
            )
        if scenario == "idor_pt_read":
            return self._exchange(
                scenario, state, "GET", f"/api/operations/pt-sales/{other.sale_id}", markers
            )
        if scenario == "idor_cash_read":
            return self._exchange(
                scenario, state, "GET", f"/api/operations/cash-sessions/{other.cash_id}", markers
            )
        if scenario == "idor_member_patch":
            return self._exchange(
                scenario,
                state,
                "PATCH",
                f"/api/operations/members/{other.member_id}",
                markers,
                body=_json_body({"notes": "cross-gym"}),
            )
        if scenario == "idor_expense_approve":
            return self._exchange(
                scenario,
                state,
                "POST",
                f"/api/operations/expenses/{other.expense_id}/approve",
                markers,
                body=b"{}",
            )
        return public_outcome(
            scenario=scenario,
            gym=state.index,
            status="unavailable",
            leak=False,
            credential=False,
            redirect_ok=True,
            cookie_ok=True,
            controls_ok=False,
        )

    def _runtime(self, scenario: str) -> dict[str, object]:
        if scenario == "schema_denied":
            passed = _ddl_denied("CREATE TABLE isolation_characterization_forbidden (id integer)")
        elif scenario == "grant_denied":
            passed = _ddl_denied("GRANT CREATE ON SCHEMA public TO PUBLIC")
        elif scenario == "missing_context_read":
            passed = _missing_context_read()
        elif scenario == "missing_context_write":
            passed = _missing_context_write()
        elif scenario == "rollback_context":
            passed = _rollback_context(self.states)
        elif scenario == "context_reuse":
            passed = _context_reuse(self.states)
        else:
            passed = False
        return public_outcome(
            scenario=scenario,
            gym=None,
            status=runtime_status(passed),
            leak=False,
            credential=False,
            redirect_ok=True,
            cookie_ok=True,
            controls_ok=passed,
        )

    def _rls(self, scenario: str, state: GymIsolation, other: GymIsolation) -> dict[str, object]:
        if scenario == "rls_own_counts":
            passed = _rls_own(state)
        elif scenario == "rls_cross_read":
            passed = _rls_cross_read(state, other)
        elif scenario == "rls_cross_update":
            passed = _rls_cross_update(state, other)
        elif scenario == "rls_file_cross":
            passed = _rls_file_cross(state, other)
        elif scenario == "rls_audit_cross":
            passed = _rls_audit_cross(state, other)
        elif scenario == "rls_aggregate":
            passed = _rls_aggregate(state, other)
        else:
            passed = False
        leaked = scenario != "rls_own_counts" and not passed
        return public_outcome(
            scenario=scenario,
            gym=state.index,
            status=runtime_status(passed),
            leak=leaked,
            credential=False,
            redirect_ok=True,
            cookie_ok=True,
            controls_ok=passed,
        )

    def _login(self, state: GymIsolation, markers: tuple[bytes, ...]) -> dict[str, object]:
        jar = state.owner_jar
        _refresh_csrf(self.client, jar)
        status, payload, set_cookies, location = self._raw(
            "POST",
            "/api/auth/login",
            headers={
                "Origin": PUBLIC_ORIGIN,
                "Content-Type": "application/json",
                "Cookie": jar.header(),
                "x-csrf-token": jar.cookies.get("gms_csrf", ""),
            },
            body=_json_body(
                {
                    "email": state.owner_email,
                    "password": state.owner_password,
                    "device_name": "isolation-client",
                }
            ),
        )
        values = _cookie_values(set_cookies)
        jar.cookies.update(values)
        flags = auth_cookie_flags(set_cookies)
        cookie_ok = required_auth_cookies(flags, secure=True) and _login_ok(
            status, payload, jar.cookies
        )
        del values
        del set_cookies
        return public_outcome(
            scenario="auth_login",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=cookie_ok,
            controls_ok=cookie_ok,
        )

    def _exchange(
        self,
        scenario: str,
        state: GymIsolation,
        method: str,
        path: str,
        markers: tuple[bytes, ...],
        body: bytes | None = None,
    ) -> dict[str, object]:
        if "/None" in path or "operations//" in path:
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(method, path, state.owner_jar, body)
        return public_outcome(
            scenario=scenario,
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=True,
        )

    def _bff(
        self,
        method: str,
        path: str,
        jar: Jar,
        body: bytes | None,
    ) -> tuple[int | str, bytes, str | None]:
        assert_isolation_route(method, path)
        _refresh_csrf(self.client, jar)
        token = jar.cookies.get("gms_csrf", "")
        headers = {
            "Origin": PUBLIC_ORIGIN,
            "Cookie": jar.header(),
            "x-csrf-token": token,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, payload, set_cookies, location = self._raw(method, path, headers=headers, body=body)
        jar.cookies.update(_cookie_values(set_cookies))
        return status, payload, location

    def _raw(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int | str, bytes, list[str], str | None]:
        assert_isolation_route(method, path)
        conn: http.client.HTTPSConnection | None = None
        try:
            conn = http.client.HTTPSConnection("localhost", 443, timeout=self.client.timeout)
            sock = socket.create_connection((self.client._ip, 443), self.client.timeout)
            tls = self.client._ssl.wrap_socket(sock, server_hostname="localhost")
            conn.sock = tls
            request_headers = {
                "Host": "localhost",
                "Accept": "application/json,text/plain",
                "Accept-Encoding": "identity",
                "Connection": "close",
                **headers,
            }
            conn.request(method, path, body=body, headers=request_headers)
            response = conn.getresponse()
            status = int(response.status)
            location = response.getheader("Location")
            set_cookies = [
                value for key, value in response.getheaders() if key.lower() == "set-cookie"
            ]
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            clipped, _truncated = clip_body(payload)
            return status, clipped, set_cookies, location
        except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException):
            return "timeout", b"", [], None
        finally:
            if conn is not None:
                conn.close()


def _cookie_values(headers: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in headers:
        pair = header.split(";", 1)[0]
        name, separator, value = pair.partition("=")
        if separator and name in {"gms_access", "gms_refresh", "gms_csrf"} and value:
            found[name] = value
    return found


def _unavailable(scenario: str, gym: int) -> dict[str, object]:
    return public_outcome(
        scenario=scenario,
        gym=gym,
        status="unavailable",
        leak=False,
        credential=False,
        redirect_ok=True,
        cookie_ok=True,
        controls_ok=False,
    )


def _location_ok(location: str | None) -> bool:
    if location is None or location == "":
        return True
    return redirect_accepted(location)


def _scrape() -> tuple[bool, int | None]:
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
    except (OSError, ValueError):
        return False, None
    return caddy_metrics_observation(text)


def _blank(states: Sequence[GymIsolation]) -> None:
    for state in states:
        state.blank()


def run_characterization(cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise IsolationError(
            "staging isolation characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise IsolationError(
            "staging isolation characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_ISOLATION_PROJECT") != _REQUIRED_PROJECT:
        raise IsolationError("staging isolation characterization requires the disposable project")
    if os.environ.get("STAGING_ISOLATION_PROFILE") != PROFILE_NAME:
        raise IsolationError("staging isolation characterization requires the fixed profile")
    for flag in (*PROOF_FLAGS, *LOCAL_ISOLATION_FLAGS, *_REFUSED_CLAIMS):
        if os.environ.get(flag) == "1":
            raise IsolationError("staging isolation characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_ISOLATION_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"].get("HOST", "")),
        database_user=str(settings.DATABASES["default"].get("USER", "")),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    resolve_profile(PROFILE_NAME)
    plan = build_plan()
    states = _seed()
    secrets = [code for code, _name, email in _GYMS for code in (code, email)]
    for state in states:
        secrets.extend(state.secrets)
        secrets.extend((state.owner_email, state.gym_code, state.member_id))
    try:
        client = Client(cafile, TIMEOUT_SECONDS)
        probe = Probe(client, states)
        before = _scrape()
        started = time.perf_counter()
        outcomes: list[dict[str, object]] = []
        cancelled = 0
        for ordinal, step in enumerate(plan):
            if time.perf_counter() - started > DURATION_SECONDS:
                cancelled = len(plan) - ordinal
                break
            scenario = step["scenario"]
            gym = step["gym"]
            if type(scenario) is not str or (gym is not None and type(gym) is not int):
                raise IsolationError("isolation plan step is invalid")
            outcomes.append(probe.run(scenario, gym if type(gym) is int else None))
        wall = time.perf_counter() - started
        after = _scrape()
        for state in states:
            secrets.extend(state.owner_jar.cookies.values())
        snapshots = _snapshot(states)
        report = assemble(
            outcomes=outcomes,
            snapshots=snapshots,
            cancelled=cancelled,
            wall_seconds=wall,
            metrics=metrics_delta(before, after),
        )
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(rendered, secrets):
            raise IsolationError("isolation evidence contains secret material")
        return report
    finally:
        _blank(states)
        secrets.clear()


def characterize(cafile: Path) -> dict[str, Any]:
    try:
        return run_characterization(cafile)
    except Exception as exc:
        report = failure_document(PROFILE_NAME, "characterization")
        report["failure_detail"] = _safe_text(exc)
        report["gate_reasons"] = ["characterization"]
        return report


class Command(BaseCommand):
    help = (
        "Run the fixed local tenant-isolation characterization. "
        "Not independent tenant-isolation proof. Not a penetration test."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        cafile = Path(os.environ.get("STAGING_ISOLATION_CA_FILE", ""))
        report = characterize(cafile)
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(
            rendered,
            (
                "ISOGYMA",
                "ISOGYMB",
                "owner-iso-a@rehearsal.invalid",
                "owner-iso-b@rehearsal.invalid",
            ),
        ):
            report = failure_document(PROFILE_NAME, "evidence")
            report["failure_detail"] = "secret material"
            rendered = json.dumps(report, sort_keys=True)
        self.stdout.write(rendered)

"""Bounded local security characterization for the disposable staging rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and sends
a fixed matrix through Caddy and one private API auth probe. It prints JSON
only. It does not prove a penetration test, a formal security review, or
production security.
"""

from __future__ import annotations

import hmac
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
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.audit.models import AuditLog
from apps.cashbook.models import CashSession
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
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
    DURATION_SECONDS,
    LOCAL_SECURITY_FLAGS,
    PRIVATE_API_HOST,
    PRIVATE_API_PATH,
    PRIVATE_API_PORT,
    PROFILE_NAME,
    PUBLIC_ORIGIN,
    TIMEOUT_SECONDS,
    UNTRUSTED_ORIGIN,
    assemble,
    assert_bff_path,
    assert_private_api_target,
    auth_cookie_flags,
    bff_required,
    build_plan,
    credential_in_body,
    csrf_failed,
    failure_document,
    make_key,
    marker_in_success,
    metrics_delta,
    offline_redirects_pass,
    origin_reflected,
    public_outcome,
    redirect_accepted,
    required_auth_cookies,
    resolve_profile,
    valid_resource_id,
)
from apps.expenses.models import Expense
from apps.members.models import CheckIn, Member
from apps.members.services import create_member, create_membership, create_plan, gym_today
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    _json_body,
    _login_ok,
    _nested_id,
    _refresh_csrf,
    _resolve,
)
from apps.tenants.management.commands.staging_pt_characterization import (
    _install_owner,
    _install_staff,
)
from apps.tenants.models import (
    ExpenseCategory,
    GymMembership,
    GymMembershipRole,
    Role,
    StaffProfile,
)
from apps.tenants.services import provision_gym
from apps.trainers.models import PTSale, ServicePackage
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
_FORGED_BFF = "forged-bff-secret-not-the-rehearsal-value"
_PROBE_EMAIL = "security-probe@rehearsal.invalid"
_PROBE_PASSWORD = "security-probe-password-value"
_GYMS = (
    ("SECGYMA", "Security Gym A", "owner-sec-a@rehearsal.invalid"),
    ("SECGYMB", "Security Gym B", "owner-sec-b@rehearsal.invalid"),
)


class SecurityError(CommandError):
    """The security characterization could not finish a fixed step."""


def _safe_text(exc: BaseException) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "@" in detail or "password" in lowered or "token" in lowered or "secret" in lowered:
        return type(exc).__name__
    if len(detail) > 180:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}"


@dataclass
class GymSecurity:
    index: int
    gym_id: UUID
    gym_code: str
    owner_email: str
    owner_password: str
    manager_email: str
    manager_password: str
    trainer_email: str
    trainer_password: str
    trainer_membership_id: str
    manager_membership_id: str
    owner_role_id: str
    member_id: str
    category_id: str
    business_date: str
    trainer_profile_id: str
    owner_jar: Jar = field(default_factory=Jar)
    manager_jar: Jar = field(default_factory=Jar)
    trainer_jar: Jar = field(default_factory=Jar)
    expense_id: str = ""
    manager_expense_id: str = ""
    cash_id: str = ""
    checkin_id: str = ""
    sale_id: str = ""
    secrets: list[str] = field(default_factory=list)

    def blank(self) -> None:
        self.owner_password = ""
        self.manager_password = ""
        self.trainer_password = ""
        self.secrets.clear()
        self.owner_jar.cookies.clear()
        self.manager_jar.cookies.clear()
        self.trainer_jar.cookies.clear()


def _seed() -> list[GymSecurity]:
    import secrets

    states: list[GymSecurity] = []
    for index, (code, name, owner_email) in enumerate(_GYMS):
        owner_password = secrets.token_urlsafe(24)
        manager_password = secrets.token_urlsafe(24)
        trainer_password = secrets.token_urlsafe(24)
        suffix = code[-1].lower()
        manager_email = f"manager-sec-{suffix}@rehearsal.invalid"
        trainer_email = f"trainer-sec-{suffix}@rehearsal.invalid"
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=owner_email)
            owner = _install_owner(provisioned.owner_membership, owner_email, owner_password)
            manager_profile = _install_staff(
                owner,
                email=manager_email,
                password=manager_password,
                role_key="MANAGER",
                staff_code=f"SECMG{code[-1]}",
                first_name="Security",
                last_name="Manager",
                display_name="Security Manager",
                title="Manager",
                commission=Decimal("0"),
            )
            trainer_profile = _install_staff(
                owner,
                email=trainer_email,
                password=trainer_password,
                role_key="TRAINER",
                staff_code=f"SECTR{code[-1]}",
                first_name="Security",
                last_name="Trainer",
                display_name="Security Trainer",
                title="Trainer",
                commission=Decimal("0"),
            )
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                today = gym_today(provisioned.gym)
                category = ExpenseCategory.objects.get(gym=provisioned.gym, code="RENT")
                owner_role = Role.objects.get(
                    gym_id=provisioned.gym.id,
                    key="OWNER",
                    is_active=True,
                    source_template__key="OWNER",
                )
                manager_membership_id = StaffProfile.objects.get(
                    gym_id=provisioned.gym.id, id=manager_profile
                ).gym_membership_id
                trainer_membership_id = StaffProfile.objects.get(
                    gym_id=provisioned.gym.id, id=trainer_profile
                ).gym_membership_id
                plan_row = create_plan(
                    gym=provisioned.gym,
                    actor=owner,
                    code="SEC30",
                    name="Security Plan",
                    description="",
                    duration_value=30,
                    duration_unit="DAY",
                    price=Decimal("0.00"),
                )
                member = create_member(
                    gym=provisioned.gym,
                    actor=owner,
                    member_number="",
                    first_name="Security",
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
                    code="SEC10",
                    name="Security PT",
                    session_count=1,
                    validity_value=30,
                    validity_unit=ServicePackage.ValidityUnit.DAY,
                    price=Decimal("0.00"),
                    request_id="security-seed",
                    ip_address=None,
                    user_agent="security",
                )
                create_assignment(
                    gym=provisioned.gym,
                    actor=owner,
                    trainer_staff_profile_id=UUID(trainer_profile),
                    member_id=member.id,
                    effective_from=today,
                    effective_until=None,
                    reason="Security characterization",
                    request_id="security-seed",
                    ip_address=None,
                    user_agent="security",
                )
                clear_gym_context()
        except Exception as exc:
            raise SecurityError(f"staging security seed failed: {_safe_text(exc)}") from None
        states.append(
            GymSecurity(
                index=index,
                gym_id=provisioned.gym.id,
                gym_code=code,
                owner_email=owner_email,
                owner_password=owner_password,
                manager_email=manager_email,
                manager_password=manager_password,
                trainer_email=trainer_email,
                trainer_password=trainer_password,
                trainer_membership_id=str(trainer_membership_id),
                manager_membership_id=str(manager_membership_id),
                owner_role_id=str(owner_role.id),
                member_id=str(member.id),
                category_id=str(category.id),
                business_date=today.isoformat(),
                trainer_profile_id=trainer_profile,
                secrets=[owner_password, manager_password, trainer_password],
            )
        )
    connection.close()
    return states


def _role_keys(membership_id: str) -> list[str]:
    keys = GymMembershipRole.objects.filter(gym_membership_id=UUID(membership_id)).values_list(
        "role__key", flat=True
    )
    return sorted(str(key) for key in keys)


def _audit_count(gym_id: UUID, action: str) -> int:
    return AuditLog.objects.filter(gym_id=gym_id, action=action).count()


def _snapshot(states: Sequence[GymSecurity]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for state in states:
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            member = Member.objects.get(id=state.member_id)
            owner = GymMembership.objects.get(gym_id=state.gym_id, is_owner=True)
            manager = GymMembership.objects.get(id=state.manager_membership_id)
            trainer = GymMembership.objects.get(id=state.trainer_membership_id)
            expenses = Expense.objects.filter(gym_id=state.gym_id)
            sales = PTSale.objects.filter(gym_id=state.gym_id)
            mismatch = (
                Expense.objects.exclude(gym_id=state.gym_id).count()
                + CheckIn.objects.exclude(gym_id=state.gym_id).count()
                + CashSession.objects.exclude(gym_id=state.gym_id).count()
                + PTSale.objects.exclude(gym_id=state.gym_id).count()
                + Member.objects.exclude(gym_id=state.gym_id).count()
                + CheckIn.objects.exclude(member__gym_id=state.gym_id).count()
                + Expense.objects.exclude(category__gym_id=state.gym_id).count()
            )
            rows.append(
                {
                    "gym": state.index,
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
                    "member_status": member.status,
                    "member_notes_blank": member.notes == "",
                    "member_gym_match": member.gym_id == state.gym_id,
                    "owner_is_owner": owner.is_owner is True,
                    "manager_is_owner": manager.is_owner is True,
                    "trainer_is_owner": trainer.is_owner is True,
                    "manager_roles": _role_keys(state.manager_membership_id),
                    "trainer_roles": _role_keys(state.trainer_membership_id),
                    "audit_expense_draft": _audit_count(state.gym_id, "EXPENSE_DRAFT_CREATED"),
                    "audit_expense_submitted": _audit_count(state.gym_id, "EXPENSE_SUBMITTED"),
                    "audit_expense_approved": _audit_count(state.gym_id, "EXPENSE_APPROVED_POSTED"),
                    "audit_pt_draft": _audit_count(state.gym_id, "PT_SALE_DRAFT_CREATED"),
                    "audit_pt_submitted": _audit_count(state.gym_id, "PT_SALE_SUBMITTED"),
                    "audit_pt_approved": _audit_count(state.gym_id, "PT_SALE_APPROVED_POSTED"),
                    "audit_checkin": _audit_count(state.gym_id, "CHECK_IN_RECORDED"),
                    "gym_mismatch": mismatch,
                }
            )
            clear_gym_context()
    return rows


class Probe:
    def __init__(self, client: Client, states: Sequence[GymSecurity]) -> None:
        self.client = client
        self.states = list(states)

    def run(self, scenario: str, gym: int | None) -> dict[str, object]:
        if gym is None:
            return self._edge(scenario)
        state = self.states[gym]
        other = self.states[1 - gym]
        markers = (other.gym_code.encode(), other.member_id.encode())
        if scenario in {"auth_login", "manager_login", "trainer_login"}:
            return self._login(scenario, state, markers)
        if scenario == "expense_create":
            return self._expense_create(state, markers, manager=False)
        if scenario == "manager_expense_create":
            return self._expense_create(state, markers, manager=True)
        if scenario == "expense_submit":
            return self._submit_expense(
                state, state.expense_id, state.owner_jar, "expense-submit", markers
            )
        if scenario == "manager_expense_submit":
            return self._submit_expense(
                state,
                state.manager_expense_id,
                state.manager_jar,
                "manager-expense-submit",
                markers,
            )
        if scenario == "cash_open":
            return self._cash_open(state, markers)
        if scenario == "checkin_create":
            return self._checkin(state, state.member_id, "checkin", markers)
        if scenario == "pt_sale_create":
            return self._sale_create(state, markers)
        if scenario == "pt_sale_submit":
            return self._sale_action(
                state, "submit", state.sale_id, state.owner_jar, "pt-submit", markers
            )
        if scenario == "idor_member_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/members/{other.member_id}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "idor_expense_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/expenses/{other.expense_id}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "idor_cash_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/cash-sessions/{other.cash_id}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "idor_checkin_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/check-ins/{other.checkin_id}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "idor_pt_sale_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/pt-sales/{other.sale_id}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "report_read":
            return self._exchange(
                scenario,
                state,
                "GET",
                f"/api/operations/reports/daily?date={state.business_date}",
                state.owner_jar,
                None,
                None,
                markers,
            )
        if scenario == "idor_member_patch":
            return self._exchange(
                scenario,
                state,
                "PATCH",
                f"/api/operations/members/{other.member_id}",
                state.owner_jar,
                _json_body({"notes": "cross-gym"}),
                make_key("member-patch", state.index, 0),
                markers,
            )
        if scenario == "idor_checkin_write":
            return self._checkin(
                state, other.member_id, "checkin-cross", markers, scenario="idor_checkin_write"
            )
        if scenario == "idor_expense_approve":
            return self._approve(
                state,
                "expenses",
                other.expense_id,
                state.owner_jar,
                "idor-expense",
                scenario,
                markers,
            )
        if scenario == "idor_pt_approve":
            return self._sale_action(
                state,
                "approve",
                other.sale_id,
                state.owner_jar,
                "idor-pt",
                markers,
                scenario=scenario,
            )
        if scenario == "mass_expense":
            return self._mass_expense(state, markers)
        if scenario == "mass_member":
            return self._mass_member(state, other, markers)
        if scenario == "self_expense_approve":
            return self._approve(
                state,
                "expenses",
                state.expense_id,
                state.owner_jar,
                "self-expense",
                scenario,
                markers,
            )
        if scenario == "manager_self_approve":
            return self._approve(
                state,
                "expenses",
                state.manager_expense_id,
                state.manager_jar,
                "manager-self",
                scenario,
                markers,
            )
        if scenario == "trainer_expense_approve":
            return self._approve(
                state,
                "expenses",
                state.expense_id,
                state.trainer_jar,
                "trainer-expense",
                scenario,
                markers,
            )
        if scenario == "self_pt_approve":
            return self._sale_action(
                state,
                "approve",
                state.sale_id,
                state.owner_jar,
                "self-pt",
                markers,
                scenario=scenario,
            )
        if scenario == "role_escalation":
            return self._escalate(state, markers)
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

    def _edge(self, scenario: str) -> dict[str, object]:
        if scenario == "redirect_offline":
            return public_outcome(
                scenario=scenario,
                gym=None,
                status=200 if offline_redirects_pass() else 400,
                leak=False,
                credential=False,
                redirect_ok=True,
                cookie_ok=True,
                controls_ok=offline_redirects_pass(),
            )
        if scenario == "edge_staff_blocked":
            status, payload, _cookies, location, _acao = self._raw(
                "GET", "/api/v1/members/", headers={}, body=None
            )
            return self._edge_outcome(
                scenario, status, payload, location, controls=b"Not found" in payload
            )
        if scenario == "csrf_absent":
            status, payload, _cookies, location, _acao = self._raw(
                "POST",
                "/api/auth/login",
                headers={"Origin": PUBLIC_ORIGIN, "Content-Type": "application/json"},
                body=_probe_body(),
            )
            return self._edge_outcome(
                scenario, status, payload, location, controls=csrf_failed(payload)
            )
        if scenario == "csrf_mismatch":
            token_a = "a" * 43
            token_b = "b" * 43
            status, payload, _cookies, location, _acao = self._raw(
                "POST",
                "/api/auth/login",
                headers={
                    "Origin": PUBLIC_ORIGIN,
                    "Content-Type": "application/json",
                    "Cookie": f"gms_csrf={token_a}",
                    "x-csrf-token": token_b,
                },
                body=_probe_body(),
            )
            return self._edge_outcome(
                scenario, status, payload, location, controls=csrf_failed(payload)
            )
        if scenario == "origin_untrusted":
            token = "c" * 43
            status, payload, _cookies, location, acao = self._raw(
                "POST",
                "/api/auth/login",
                headers={
                    "Origin": UNTRUSTED_ORIGIN,
                    "Content-Type": "application/json",
                    "Cookie": f"gms_csrf={token}",
                    "x-csrf-token": token,
                },
                body=_probe_body(),
            )
            reflected = origin_reflected(acao, UNTRUSTED_ORIGIN)
            return self._edge_outcome(
                scenario,
                status,
                payload,
                location,
                controls=csrf_failed(payload) and not reflected,
            )
        if scenario == "session_absent":
            status, payload, _cookies, location, _acao = self._raw(
                "GET", "/api/operations/members", headers={}, body=None
            )
            return self._edge_outcome(scenario, status, payload, location, controls=True)
        if scenario == "session_invalid":
            status, payload, _cookies, location, _acao = self._raw(
                "GET",
                "/api/operations/members",
                headers={"Cookie": "gms_access=not-a-jwt"},
                body=None,
            )
            return self._edge_outcome(scenario, status, payload, location, controls=True)
        if scenario in {"bff_secret_missing", "bff_secret_forged"}:
            return self._private(scenario)
        return public_outcome(
            scenario=scenario,
            gym=None,
            status="unavailable",
            leak=False,
            credential=False,
            redirect_ok=True,
            cookie_ok=True,
            controls_ok=False,
        )

    def _edge_outcome(
        self,
        scenario: str,
        status: int | str,
        payload: bytes,
        location: str | None,
        *,
        controls: bool,
    ) -> dict[str, object]:
        markers = tuple(code.encode() for code, _name, _email in _GYMS)
        return public_outcome(
            scenario=scenario,
            gym=None,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=controls,
        )

    def _private(self, scenario: str) -> dict[str, object]:
        configured = str(getattr(settings, "BFF_SHARED_SECRET", ""))
        collided = len(configured) == len(_FORGED_BFF) and hmac.compare_digest(
            _FORGED_BFF, configured
        )
        if len(_FORGED_BFF) < 32 or collided:
            raise SecurityError("forged probe collided")
        header = _FORGED_BFF if scenario == "bff_secret_forged" else ""
        status, payload, location = _private_auth(header)
        return public_outcome(
            scenario=scenario,
            gym=None,
            status=status,
            leak=False,
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=bff_required(payload),
        )

    def _login(
        self, scenario: str, state: GymSecurity, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        if scenario == "manager_login":
            email, password, jar = state.manager_email, state.manager_password, state.manager_jar
        elif scenario == "trainer_login":
            email, password, jar = state.trainer_email, state.trainer_password, state.trainer_jar
        else:
            email, password, jar = state.owner_email, state.owner_password, state.owner_jar
        _refresh_csrf(self.client, jar)
        status, payload, set_cookies, location, _acao = self._raw(
            "POST",
            "/api/auth/login",
            headers={
                "Origin": PUBLIC_ORIGIN,
                "Content-Type": "application/json",
                "Cookie": jar.header(),
                "x-csrf-token": jar.cookies.get("gms_csrf", ""),
            },
            body=_json_body(
                {"email": email, "password": password, "device_name": "security-client"}
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
            scenario=scenario,
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=cookie_ok,
            controls_ok=cookie_ok,
        )

    def _expense_body(self, state: GymSecurity, *, description: str, injected: bool) -> bytes:
        payload: dict[str, object] = {
            "category_id": state.category_id,
            "business_date": state.business_date,
            "description": description,
            "amount": "1.00",
            "currency": "THB",
            "payment_method": "BANK_TRANSFER",
        }
        if injected:
            payload["status"] = "POSTED"
            payload["gym_id"] = str(state.gym_id)
            payload["approved_by_membership_id"] = state.manager_membership_id
        return _json_body(payload)

    def _expense_create(
        self, state: GymSecurity, markers: tuple[bytes, ...], *, manager: bool
    ) -> dict[str, object]:
        scenario = "manager_expense_create" if manager else "expense_create"
        jar = state.manager_jar if manager else state.owner_jar
        kind = "manager-expense" if manager else "expense"
        description = "Security manager draft" if manager else "Security draft"
        status, payload, location = self._bff(
            "POST",
            "/api/operations/expenses",
            jar,
            self._expense_body(state, description=description, injected=False),
            make_key(kind, state.index, 0),
        )
        found = valid_resource_id(_nested_id(payload, ("data", "id")))
        if status == 201 and found:
            if manager:
                state.manager_expense_id = found
            else:
                state.expense_id = found
        return public_outcome(
            scenario=scenario,
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=bool(found) if status == 201 else True,
        )

    def _mass_expense(self, state: GymSecurity, markers: tuple[bytes, ...]) -> dict[str, object]:
        status, payload, location = self._bff(
            "POST",
            "/api/operations/expenses",
            state.owner_jar,
            self._expense_body(state, description="Security injected", injected=True),
            make_key("expense-mass", state.index, 0),
        )
        return public_outcome(
            scenario="mass_expense",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=status == 400,
        )

    def _submit_expense(
        self,
        state: GymSecurity,
        expense_id: str,
        jar: Jar,
        kind: str,
        markers: tuple[bytes, ...],
    ) -> dict[str, object]:
        scenario = "manager_expense_submit" if kind.startswith("manager") else "expense_submit"
        target = valid_resource_id(expense_id)
        if not target:
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(
            "POST",
            f"/api/operations/expenses/{target}/submit",
            jar,
            b"{}",
            make_key(kind, state.index, 0),
        )
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

    def _cash_open(self, state: GymSecurity, markers: tuple[bytes, ...]) -> dict[str, object]:
        status, payload, location = self._bff(
            "POST",
            "/api/operations/cash-sessions/open",
            state.owner_jar,
            _json_body(
                {
                    "register_code": "SECUR",
                    "business_date": state.business_date,
                    "opening_cash": "0.00",
                }
            ),
            make_key("cash-open", state.index, 0),
        )
        found = valid_resource_id(_nested_id(payload, ("data", "session", "id")))
        if status == 201 and found:
            state.cash_id = found
        return public_outcome(
            scenario="cash_open",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=bool(found) if status == 201 else True,
        )

    def _checkin(
        self,
        state: GymSecurity,
        member_id: str,
        kind: str,
        markers: tuple[bytes, ...],
        *,
        scenario: str = "checkin_create",
    ) -> dict[str, object]:
        target = valid_resource_id(member_id)
        if not target:
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(
            "POST",
            "/api/operations/check-ins",
            state.owner_jar,
            _json_body({"member_id": target, "note": "Security visit"}),
            make_key(kind, state.index, 0),
        )
        found = valid_resource_id(_nested_id(payload, ("data", "id")))
        if scenario == "checkin_create" and status == 201 and found:
            state.checkin_id = found
        return public_outcome(
            scenario=scenario,
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=bool(found) if scenario == "checkin_create" and status == 201 else True,
        )

    def _sale_create(self, state: GymSecurity, markers: tuple[bytes, ...]) -> dict[str, object]:
        status, payload, location = self._bff(
            "POST",
            "/api/operations/pt-sales",
            state.owner_jar,
            _json_body(
                {
                    "member_id": state.member_id,
                    "trainer_staff_profile_id": state.trainer_profile_id,
                    "service_date": state.business_date,
                    "description": "Security session",
                    "gross_fee": "1.00",
                    "currency": "THB",
                }
            ),
            make_key("pt-sale", state.index, 0),
        )
        found = valid_resource_id(_nested_id(payload, ("data", "id")))
        if status == 201 and found:
            state.sale_id = found
        return public_outcome(
            scenario="pt_sale_create",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=bool(found) if status == 201 else True,
        )

    def _sale_action(
        self,
        state: GymSecurity,
        action: str,
        sale_id: str,
        jar: Jar,
        kind: str,
        markers: tuple[bytes, ...],
        *,
        scenario: str = "pt_sale_submit",
    ) -> dict[str, object]:
        target = valid_resource_id(sale_id)
        if not target:
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(
            "POST",
            f"/api/operations/pt-sales/{target}/{action}",
            jar,
            b"{}",
            make_key(kind, state.index, 0),
        )
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

    def _approve(
        self,
        state: GymSecurity,
        collection: str,
        resource_id: str,
        jar: Jar,
        kind: str,
        scenario: str,
        markers: tuple[bytes, ...],
    ) -> dict[str, object]:
        target = valid_resource_id(resource_id)
        if not target:
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(
            "POST",
            f"/api/operations/{collection}/{target}/approve",
            jar,
            b"{}",
            make_key(kind, state.index, 0),
        )
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

    def _mass_member(
        self, state: GymSecurity, other: GymSecurity, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        status, payload, location = self._bff(
            "PATCH",
            f"/api/operations/members/{state.member_id}",
            state.trainer_jar,
            _json_body(
                {
                    "notes": "injected",
                    "status": "ARCHIVED",
                    "gym_id": str(other.gym_id),
                    "linked_user_id": state.trainer_membership_id,
                    "is_owner": True,
                }
            ),
            None,
        )
        return public_outcome(
            scenario="mass_member",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=status == 400,
        )

    def _escalate(self, state: GymSecurity, markers: tuple[bytes, ...]) -> dict[str, object]:
        target = valid_resource_id(state.trainer_membership_id)
        role_id = valid_resource_id(state.owner_role_id)
        if not target or not role_id:
            return _unavailable("role_escalation", state.index)
        status, payload, location = self._bff(
            "PUT",
            f"/api/operations/gym-memberships/{target}/roles",
            state.trainer_jar,
            _json_body({"role_ids": [role_id], "is_owner": True}),
            None,
        )
        return public_outcome(
            scenario="role_escalation",
            gym=state.index,
            status=status,
            leak=marker_in_success(status, payload, markers),
            credential=credential_in_body(payload),
            redirect_ok=_location_ok(location),
            cookie_ok=True,
            controls_ok=status == 403,
        )

    def _exchange(
        self,
        scenario: str,
        state: GymSecurity,
        method: str,
        path: str,
        jar: Jar,
        body: bytes | None,
        idempotency: str | None,
        markers: tuple[bytes, ...],
    ) -> dict[str, object]:
        if "/None" in path or path.endswith("/") or "operations//" in path:
            return _unavailable(scenario, state.index)
        if any(part == "" for part in path.split("/") if "{" not in part and part.endswith("}")):
            return _unavailable(scenario, state.index)
        if not _path_has_id(path) and scenario != "report_read":
            return _unavailable(scenario, state.index)
        status, payload, location = self._bff(method, path, jar, body, idempotency)
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
        idempotency: str | None,
    ) -> tuple[int | str, bytes, str | None]:
        assert_bff_path(method, path)
        _refresh_csrf(self.client, jar)
        token = jar.cookies.get("gms_csrf", "")
        headers = {
            "Origin": PUBLIC_ORIGIN,
            "Cookie": jar.header(),
            "x-csrf-token": token,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        status, payload, set_cookies, location, _acao = self._raw(
            method, path, headers=headers, body=body
        )
        jar.cookies.update(_cookie_values(set_cookies))
        return status, payload, location

    def _raw(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int | str, bytes, list[str], str | None, str | None]:
        assert_bff_path(method, path)
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
            acao = response.getheader("Access-Control-Allow-Origin")
            set_cookies = [
                value for key, value in response.getheaders() if key.lower() == "set-cookie"
            ]
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            clipped, _truncated = clip_body(payload)
            return status, clipped, set_cookies, location, acao
        except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException):
            return "timeout", b"", [], None, None
        finally:
            if conn is not None:
                conn.close()


def _path_has_id(path: str) -> bool:
    return any(valid_resource_id(part) for part in path.split("/")) or "?date=" in path


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


def _probe_body() -> bytes:
    return _json_body(
        {"email": _PROBE_EMAIL, "password": _PROBE_PASSWORD, "device_name": "security-probe"}
    )


def _cookie_values(headers: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in headers:
        pair = header.split(";", 1)[0]
        name, separator, value = pair.partition("=")
        if separator and name in {"gms_access", "gms_refresh", "gms_csrf"} and value:
            found[name] = value
    return found


def _private_auth(forged: str) -> tuple[int | str, bytes, str | None]:
    ips = _resolve(PRIVATE_API_HOST, PRIVATE_API_PORT)
    assert_private_api_target(PRIVATE_API_HOST, PRIVATE_API_PORT, PRIVATE_API_PATH, ips)
    own = _own_ips()
    candidates = [ip for ip in ips if ip not in own] or list(ips)
    last: tuple[int | str, bytes, str | None] = ("timeout", b"", None)
    for ip in candidates:
        result = _private_once(ip, forged)
        if result[0] != "timeout":
            return result
        last = result
    return last


def _own_ips() -> set[str]:
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(str(info[4][0]))
    except OSError:
        return found
    return found


def _private_once(ip: str, forged: str) -> tuple[int | str, bytes, str | None]:
    headers = {
        "Host": PRIVATE_API_HOST,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "X-Forwarded-Proto": "https",
    }
    if forged:
        headers["X-Gym-BFF-Secret"] = forged
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(ip, PRIVATE_API_PORT, timeout=TIMEOUT_SECONDS)
        conn.request("POST", PRIVATE_API_PATH, body=b"{}", headers=headers)
        response = conn.getresponse()
        status = int(response.status)
        location = response.getheader("Location")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        clipped, _truncated = clip_body(payload)
        return status, clipped, location
    except (TimeoutError, OSError, http.client.HTTPException):
        return "timeout", b"", None
    finally:
        if conn is not None:
            conn.close()


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


def _blank(states: Sequence[GymSecurity]) -> None:
    for state in states:
        state.blank()


def run_characterization(cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise SecurityError(
            "staging security characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise SecurityError(
            "staging security characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_SECURITY_PROJECT") != _REQUIRED_PROJECT:
        raise SecurityError("staging security characterization requires the disposable project")
    if os.environ.get("STAGING_SECURITY_PROFILE") != PROFILE_NAME:
        raise SecurityError("staging security characterization requires the fixed profile")
    refused = (*PROOF_FLAGS, *LOCAL_SECURITY_FLAGS)
    for flag in (*refused, *_REFUSED_CLAIMS):
        if os.environ.get(flag) == "1":
            raise SecurityError("staging security characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_SECURITY_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"].get("HOST", "")),
        database_user=str(settings.DATABASES["default"].get("USER", "")),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    resolve_profile(PROFILE_NAME)
    plan = build_plan()
    states = _seed()
    secrets = [_PROBE_PASSWORD, _FORGED_BFF, _PROBE_EMAIL]
    for state in states:
        secrets.extend(state.secrets)
        secrets.extend(
            (state.owner_email, state.manager_email, state.trainer_email, state.gym_code)
        )
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
                raise SecurityError("security plan step is invalid")
            outcomes.append(probe.run(scenario, gym if type(gym) is int else None))
        wall = time.perf_counter() - started
        after = _scrape()
        for state in states:
            secrets.extend(state.owner_jar.cookies.values())
            secrets.extend(state.manager_jar.cookies.values())
            secrets.extend(state.trainer_jar.cookies.values())
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
            raise SecurityError("security evidence contains secret material")
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
    help = "Run the fixed local security characterization. Not a penetration test."

    def handle(self, *args: Any, **options: Any) -> None:
        cafile = Path(os.environ.get("STAGING_SECURITY_CA_FILE", ""))
        report = characterize(cafile)
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(
            rendered,
            (
                _PROBE_PASSWORD,
                _FORGED_BFF,
                _PROBE_EMAIL,
                "SECGYMA",
                "SECGYMB",
                "owner-sec-a@rehearsal.invalid",
                "owner-sec-b@rehearsal.invalid",
            ),
        ):
            report = failure_document(PROFILE_NAME, "evidence")
            report["failure_detail"] = "secret material"
            rendered = json.dumps(report, sort_keys=True)
        self.stdout.write(rendered)

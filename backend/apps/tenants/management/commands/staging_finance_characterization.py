"""Bounded receipt and cash-close characterization for the staging rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and sends
fixed requests through Caddy. It prints metric JSON only. It does not prove a
load test, soak, capacity, SLO, or financial reconciliation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from apps.cashbook.models import CashSession
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.common.finance_characterization import (
    COVERAGE,
    PLANNED_CLOSING,
    PLANNED_MOVEMENT,
    PLANNED_OPENING,
    PROFILE_NAME,
    PROOF_FLAGS,
    assert_finance_path,
    assert_rehearsal_context,
    assess_finance,
    build_plan,
    classify_status,
    closing_matches,
    failure_document,
    judge,
    make_key,
    public_outcome,
    records_match,
    resolve_profile,
    scenarios_match,
    summarize_scenario,
)
from apps.common.load_characterization import marker_present, material_leaked, percentile_ms
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    LoadError,
    _json_body,
    _login_ok,
    _metrics_5xx,
    _nested_id,
    _postgres_peak,
    _refresh_csrf,
    _sample_postgres,
)
from apps.tenants.models import GymMembership, Role
from apps.tenants.services import (
    _create_pending_owner_invitation,
    accept_owner_invitation,
    provision_gym,
)
from apps.tenants.staff_services import accept_staff_invitation, invite_staff

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
    (
        "FINCGYMA",
        "Finance Gym A",
        "fin-owner-a@rehearsal.invalid",
        "fin-approver-a@rehearsal.invalid",
    ),
    (
        "FINCGYMB",
        "Finance Gym B",
        "fin-owner-b@rehearsal.invalid",
        "fin-approver-b@rehearsal.invalid",
    ),
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_MONEY = re.compile(r"\d+\.\d{2}\Z")


class FinanceError(RuntimeError):
    """The characterization failed closed without a secret in the message."""


@dataclass
class GymFinance:
    index: int
    gym_id: UUID
    gym_code: str
    owner_email: str
    owner_password: str
    approver_email: str
    approver_password: str
    owner_jar: Jar
    approver_jar: Jar
    business_date: str
    cash_id: str = ""
    payment_id: str = ""
    receipt_id: str = ""
    approve_key: str = ""
    close_key: str = ""
    approve_body: bytes = b""
    close_body: bytes = b""
    passwords: list[str] = field(default_factory=list)


def _safe_text(exc: BaseException) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "@" in detail or "password" in lowered or "token" in lowered or len(detail) > 180:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}"


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


def _money(payload: bytes, path: tuple[str, ...]) -> str:
    value = _walk(payload, path)
    if not isinstance(value, str) or _MONEY.fullmatch(value) is None:
        return ""
    return value


def _text(payload: bytes, path: tuple[str, ...]) -> str:
    value = _walk(payload, path)
    if not isinstance(value, str):
        return ""
    return value


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
            request_id="finance-seed",
            ip_address=None,
            user_agent="finance",
        )
    finally:
        del token


def _staff_token() -> str:
    box = getattr(mail, "outbox", None)
    if not isinstance(box, list) or not box:
        raise FinanceError("staff invitation was not captured")
    message = box[-1].body
    if not isinstance(message, str):
        raise FinanceError("staff invitation was not captured")
    marker = "token="
    if marker not in message:
        raise FinanceError("staff invitation was not captured")
    token = message.split(marker, 1)[1].split()[0].strip()
    if len(token) < 16:
        raise FinanceError("staff invitation was not captured")
    return token


def _install_approver(owner: GymMembership, email: str, password: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=owner.gym_id)
        role = Role.objects.get(
            gym_id=owner.gym_id,
            key="MANAGER",
            is_active=True,
            source_template__key="MANAGER",
        )
        today = gym_today(owner.gym)
    invite_staff(
        gym_id=owner.gym_id,
        actor=owner.user,
        actor_membership_id=owner.id,
        staff_data={
            "email": email,
            "first_name": "Load",
            "last_name": "Approver",
            "staff_code": f"FINCMG{owner.gym.code[-1]}",
            "display_name": "Load Approver",
            "joined_date": today,
            "employment_title": "Manager",
            "default_commission_percent": Decimal("0"),
        },
        role_ids=[role.id],
        request_id="finance-seed",
        user_agent="finance",
    )
    token = _staff_token()
    try:
        accept_staff_invitation(
            plaintext_token=token,
            password=password,
            confirmation={
                "display_name": "Load Approver",
                "first_name": "Load",
                "last_name": "Approver",
            },
            request_id="finance-seed",
            user_agent="finance",
        )
    finally:
        del token
        box = getattr(mail, "outbox", None)
        if isinstance(box, list):
            box.clear()


def _seed() -> list[GymFinance]:
    states: list[GymFinance] = []
    for index, (code, name, owner_email, approver_email) in enumerate(_GYMS):
        owner_password = secrets.token_urlsafe(24)
        approver_password = secrets.token_urlsafe(24)
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=owner_email)
            owner = _install_owner(provisioned.owner_membership, owner_email, owner_password)
            _install_approver(owner, approver_email, approver_password)
            today = gym_today(provisioned.gym)
        except Exception as exc:
            raise FinanceError(f"staging finance seed failed: {_safe_text(exc)}") from None
        states.append(
            GymFinance(
                index=index,
                gym_id=provisioned.gym.id,
                gym_code=code,
                owner_email=owner_email,
                owner_password=owner_password,
                approver_email=approver_email,
                approver_password=approver_password,
                owner_jar=Jar(),
                approver_jar=Jar(),
                business_date=today.isoformat(),
                passwords=[owner_password, approver_password],
            )
        )
    connection.close()
    return states


def _blank(states: Sequence[GymFinance]) -> None:
    for state in states:
        state.owner_password = ""
        state.approver_password = ""
        state.passwords.clear()
        state.owner_jar.cookies.clear()
        state.approver_jar.cookies.clear()


def _money_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _gym_snapshot(state: GymFinance) -> dict[str, object]:
    with transaction.atomic():
        set_gym_context(gym_id=state.gym_id)
        receipts = list(Receipt.objects.all())
        payments = list(Payment.objects.all())
        sessions = list(CashSession.objects.all())
        clear_gym_context()
    receipt = receipts[0] if len(receipts) == 1 else None
    payment = payments[0] if len(payments) == 1 else None
    session = sessions[0] if len(sessions) == 1 else None
    return {
        "receipt_count": len(receipts),
        "receipt_status": "" if receipt is None else receipt.status,
        "payment_count": len(payments),
        "payment_status": "" if payment is None else payment.status,
        "payment_method": "" if payment is None else payment.method,
        "payment_amount": "" if payment is None else _money_text(payment.amount),
        "session_count": len(sessions),
        "session_status": "" if session is None else session.status,
        "opening_cash": "" if session is None else _money_text(session.opening_cash),
        "cash_sales": "" if session is None else _money_text(session.cash_sales),
        "expected_closing": "" if session is None else _money_text(session.expected_closing),
        "actual_closing": "" if session is None else _money_text(session.actual_closing),
        "difference": "" if session is None else _money_text(session.difference),
        "closed": "yes" if session is not None and session.closed_at is not None else "no",
        "receipt_number_present": "yes" if receipt is not None and receipt.receipt_number else "no",
        "distinct_receipt_numbers": len({row.receipt_number for row in receipts}),
        "issued": sum(1 for row in receipts if row.status == "ISSUED"),
        "posted": sum(1 for row in payments if row.status == "POSTED"),
        "closed_sessions": sum(1 for row in sessions if row.status == "CLOSED"),
        "difference_nonzero": sum(
            1 for row in sessions if row.difference is None or row.difference != Decimal("0.00")
        ),
    }


def _count_field(snapshot: Mapping[str, object], key: str) -> int:
    value = snapshot[key]
    if type(value) is int:
        return value
    raise FinanceError("staging finance count was invalid")


def _observed(states: Sequence[GymFinance]) -> dict[str, int] | None:
    snapshots = [_gym_snapshot(state) for state in states]
    connection.close()
    mismatch = 0
    for snapshot in snapshots:
        empty = (
            _count_field(snapshot, "receipt_count") == 0
            and _count_field(snapshot, "payment_count") == 0
            and _count_field(snapshot, "session_count") == 0
        )
        if not empty and not records_match(snapshot):
            mismatch += 1
    return {
        "receipts_issued": sum(_count_field(snapshot, "issued") for snapshot in snapshots),
        "payments_posted": sum(_count_field(snapshot, "posted") for snapshot in snapshots),
        "cash_sessions": sum(_count_field(snapshot, "session_count") for snapshot in snapshots),
        "cash_closed": sum(_count_field(snapshot, "closed_sessions") for snapshot in snapshots),
        "difference_nonzero": sum(
            _count_field(snapshot, "difference_nonzero") for snapshot in snapshots
        ),
        "distinct_receipt_numbers": sum(
            _count_field(snapshot, "distinct_receipt_numbers") for snapshot in snapshots
        ),
        "gym_mismatch": mismatch,
    }


class Runner:
    def __init__(self, client: Client, states: Sequence[GymFinance]) -> None:
        self.client = client
        self.states = list(states)

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

    def _state(self, index: object) -> GymFinance:
        if type(index) is not int or index not in (0, 1):
            raise FinanceError("staging finance gym selector is invalid")
        return self.states[index]

    def _other(self, state: GymFinance) -> GymFinance:
        return self.states[1 - state.index]

    def _shape_ok(self, scenario: str, payload: bytes) -> bool:
        if scenario == "receipt_download":
            lowered = payload.lower()
            return b"internal receipt" in lowered and b"not a tax invoice" in lowered
        if scenario == "cash_expected":
            return _text(payload, ("data", "status")) == "OPEN" and closing_matches(
                _money(payload, ("data", "expected_closing"))
            )
        if scenario in {"cash_close", "cash_close_replay"}:
            return _text(payload, ("data", "session", "status")) == "CLOSED"
        return True

    def _perform(self, step: Mapping[str, object]) -> tuple[object, bytes, bool, bool]:
        scenario = str(step["scenario"])
        state = self._state(step.get("gym"))
        other = self._other(state)
        if scenario == "auth_login_owner":
            return self._login(
                scenario, state.owner_jar, state.owner_email, state.owner_password, state
            )
        if scenario == "auth_login_approver":
            return self._login(
                scenario,
                state.approver_jar,
                state.approver_email,
                state.approver_password,
                state,
            )
        if scenario == "cash_open":
            body = _json_body(
                {
                    "register_code": "FINC",
                    "business_date": state.business_date,
                    "opening_cash": PLANNED_OPENING,
                }
            )
            status, payload, leak, credential = self._call(
                scenario,
                "POST",
                "/api/operations/cash-sessions/open",
                jar=state.owner_jar,
                body=body,
                csrf=True,
                idempotency=make_key("cash-open", state.index, 0),
                state=state,
                other=other,
            )
            if status == 201:
                state.cash_id = _nested_id(payload, ("data", "session", "id"))
            return status, payload, leak, credential
        if scenario == "payment_create":
            body = _json_body(
                {
                    "purpose": "OTHER",
                    "description": "Finance draft",
                    "amount": PLANNED_MOVEMENT,
                    "currency": "THB",
                    "method": "CASH",
                    "business_date": state.business_date,
                }
            )
            status, payload, leak, credential = self._call(
                scenario,
                "POST",
                "/api/operations/payments",
                jar=state.owner_jar,
                body=body,
                csrf=True,
                idempotency=make_key("payment", state.index, 0),
                state=state,
                other=other,
            )
            if status == 201:
                state.payment_id = _nested_id(payload, ("data", "id"))
            return status, payload, leak, credential
        if scenario == "payment_submit":
            if not state.payment_id:
                return "unavailable", b"", False, False
            return self._call(
                scenario,
                "POST",
                f"/api/operations/payments/{state.payment_id}/submit",
                jar=state.owner_jar,
                body=b"{}",
                csrf=True,
                idempotency=make_key("submit", state.index, 0),
                state=state,
                other=other,
            )
        if scenario in {"payment_self_approve", "payment_approve", "payment_approve_replay"}:
            return self._approve(scenario, state, other)
        if scenario == "receipt_download":
            if not state.receipt_id:
                return "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/receipts/{state.receipt_id}/download",
                jar=state.owner_jar,
                state=state,
                other=other,
            )
        if scenario == "cash_expected":
            if not state.cash_id:
                return "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/cash-sessions/{state.cash_id}",
                jar=state.owner_jar,
                state=state,
                other=other,
            )
        if scenario in {"cash_close", "cash_close_replay", "cash_close_immutable"}:
            return self._close(scenario, state, other, state.cash_id, state.owner_jar)
        if scenario == "cross_receipt_download":
            if not other.receipt_id:
                return "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/receipts/{other.receipt_id}/download",
                jar=state.owner_jar,
                state=state,
                other=other,
            )
        if scenario == "cross_cash_close":
            return self._close(scenario, state, other, other.cash_id, state.owner_jar)
        if scenario == "cross_payment_approve":
            if not other.payment_id or not state.cash_id:
                return "unavailable", b"", False, False
            body = _json_body({"cash_session_id": state.cash_id})
            return self._call(
                scenario,
                "POST",
                f"/api/operations/payments/{other.payment_id}/approve",
                jar=state.approver_jar,
                body=body,
                csrf=True,
                idempotency=make_key("cross-approve", state.index, 0),
                state=state,
                other=other,
            )
        raise FinanceError("staging finance scenario is invalid")

    def _approve(
        self, scenario: str, state: GymFinance, other: GymFinance
    ) -> tuple[object, bytes, bool, bool]:
        if not state.payment_id or not state.cash_id:
            return "unavailable", b"", False, False
        if scenario == "payment_self_approve":
            jar = state.owner_jar
            key = make_key("self-approve", state.index, 0)
            body = _json_body({"cash_session_id": state.cash_id})
        else:
            jar = state.approver_jar
            if not state.approve_key:
                state.approve_key = make_key("approve", state.index, 0)
                state.approve_body = _json_body({"cash_session_id": state.cash_id})
            key = state.approve_key
            body = state.approve_body
        status, payload, leak, credential = self._call(
            scenario,
            "POST",
            f"/api/operations/payments/{state.payment_id}/approve",
            jar=jar,
            body=body,
            csrf=True,
            idempotency=key,
            state=state,
            other=other,
        )
        if scenario == "payment_approve" and status == 200:
            state.receipt_id = _nested_id(payload, ("data", "receipt", "id"))
        return status, payload, leak, credential

    def _close(
        self,
        scenario: str,
        state: GymFinance,
        other: GymFinance,
        session_id: str,
        jar: Jar,
    ) -> tuple[object, bytes, bool, bool]:
        if not session_id:
            return "unavailable", b"", False, False
        if scenario == "cash_close_immutable":
            key = make_key("close-again", state.index, 0)
            body = _json_body({"actual_closing": PLANNED_CLOSING, "difference_reason": ""})
        elif scenario == "cross_cash_close":
            key = make_key("cross-close", state.index, 0)
            body = _json_body({"actual_closing": PLANNED_CLOSING, "difference_reason": ""})
        else:
            if not state.close_key:
                state.close_key = make_key("close", state.index, 0)
                state.close_body = _json_body(
                    {"actual_closing": PLANNED_CLOSING, "difference_reason": ""}
                )
            key = state.close_key
            body = state.close_body
        return self._call(
            scenario,
            "POST",
            f"/api/operations/cash-sessions/{session_id}/reconcile",
            jar=jar,
            body=body,
            csrf=True,
            idempotency=key,
            state=state,
            other=other,
        )

    def _login(
        self,
        scenario: str,
        jar: Jar,
        email: str,
        password: str,
        state: GymFinance,
    ) -> tuple[object, bytes, bool, bool]:
        if len(jar.cookies.get("gms_csrf", "")) < 32:
            assert_finance_path("/api/auth/csrf")
            _refresh_csrf(self.client, jar)
        body = _json_body({"email": email, "password": password, "device_name": "fin-client"})
        status, payload, leak, credential = self._call(
            scenario,
            "POST",
            "/api/auth/login",
            jar=jar,
            body=body,
            csrf=True,
            state=state,
            other=self._other(state),
            extra_markers=(password,),
        )
        if not _login_ok(status, payload, jar.cookies):
            status = status if status != 200 else "login_rejected"
        return status, payload, leak, credential

    def _call(
        self,
        scenario: str,
        method: str,
        path: str,
        *,
        jar: Jar,
        state: GymFinance,
        other: GymFinance,
        body: bytes | None = None,
        csrf: bool = False,
        idempotency: str | None = None,
        extra_markers: Sequence[str] = (),
    ) -> tuple[object, bytes, bool, bool]:
        assert_finance_path(path)
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
            for item in (other.gym_code, other.receipt_id, other.payment_id, other.cash_id)
            if len(item) >= 8
        ]
        leak = status in {200, 201} and marker_present(
            payload, [item.encode("utf-8") for item in other_markers]
        )
        return status, payload, leak, credential


def _base_report(profile: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": "local-disposable-finance-characterization",
        "profile": profile,
        "local_finance_status": "not-run",
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
        "reconciliation": {"transport": "service-only", "matched": False},
        "evidence_name": "finance.json",
        "transport_note": (
            "Browser login, payment draft, submit, approval, receipt download, "
            "and cash reconcile are HTTP through Caddy and the BFF. Gym, owner, "
            "and manager setup, plus database reconciliation, are service-only."
        ),
        "architecture_note": (
            "Local arm64 or amd64-emulated ClamAV on a 4 GiB Docker VM cannot "
            "validate the 8 GiB production planning budget. These ceilings are "
            "not an approved SLO. No soak profile is defined."
        ),
        "caddy_note": (
            "caddy_metrics_observed is false and caddy_5xx_delta is null when "
            "the private metrics endpoint does not answer. That is unavailable, "
            "not zero."
        ),
    }
    for flag in PROOF_FLAGS:
        report[flag] = False
    return report


def _cross_ok(
    plan: Sequence[Mapping[str, object]], outcomes: Sequence[Mapping[str, object]]
) -> bool:
    names = ("cross_receipt_download", "cross_cash_close", "cross_payment_approve")
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
    observed: dict[str, int] | None,
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
    financial_status, matched, corruption = assess_finance(plan, outcomes, observed)
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
                **(observed or {}),
            },
        }
    )
    report["local_finance_status"] = financial_status
    status, reasons = judge(report)
    report["local_finance_status"] = status
    report["gate_reasons"] = reasons
    report["connection_ceiling"] = (
        "pass" if status == "pass" or "connection_ceiling" not in reasons else "fail"
    )
    if "connection_ceiling" in reasons:
        report["connection_ceiling"] = "fail"
    return report


def run_characterization(profile_name: str, cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise FinanceError(
            "staging finance characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_FINANCE_PROJECT") != _REQUIRED_PROJECT:
        raise FinanceError("staging finance characterization requires the disposable project")
    for flag in _REFUSED_LIVE_PROOF:
        if os.environ.get(flag) == "1":
            raise FinanceError("staging finance characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_FINANCE_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"]["HOST"]),
        database_user=str(settings.DATABASES["default"]["USER"]),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise FinanceError("staging finance characterization requires the rehearsal flag")
    profile = resolve_profile(profile_name)
    if os.environ.get("STAGING_FINANCE_PROFILE") != profile.name:
        raise FinanceError("staging finance profile was not the fixed rehearsal profile")
    plan = build_plan(profile.name)
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
            "Finance Gym A",
            "Finance Gym B",
        )
    ]
    client = Client(cafile, profile.timeout_seconds)
    runner = Runner(client, states)
    outcomes: list[dict[str, object]] = []
    cancelled = 0
    fatal = False
    caddy_before = _metrics_5xx()
    samples: list[tuple[int, int]] = []
    stop = threading.Event()

    def sample() -> None:
        try:
            while not stop.is_set():
                samples.append(_sample_postgres())
                stop.wait(0.5)
        except (DatabaseError, OSError, FinanceError, LoadError, ValueError):
            samples.append((-1, -1))
        finally:
            connection.close()

    sampler = threading.Thread(target=sample, name="finance-postgres")
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
        caddy_after = _metrics_5xx()
        stop.set()
        sampler.join(5)
        postgres_peak, postgres_max = _postgres_peak(samples)
        for state in states:
            sensitive.extend((state.cash_id, state.payment_id, state.receipt_id, str(state.gym_id)))
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
            raise FinanceError("staging finance report failed redaction")
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
        report["local_finance_status"] = "fail"
        report["gate_reasons"] = ["characterization"]
        return report


class Command(BaseCommand):
    help = "Run the fixed staging finance characterization. Not a production proof."

    def handle(self, *args: object, **options: object) -> None:
        profile = os.environ.get("STAGING_FINANCE_PROFILE", "")
        cafile = Path(os.environ.get("STAGING_FINANCE_CA_FILE", ""))
        report = characterize(profile, cafile)
        self.stdout.write(
            json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
        if report.get("local_finance_status") != "pass":
            raise CommandError("local finance characterization failed")

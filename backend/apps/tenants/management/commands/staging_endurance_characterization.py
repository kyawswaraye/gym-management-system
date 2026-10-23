"""Bounded local endurance characterization for the disposable staging rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and sends
a fixed paced schedule through Caddy. It prints metric JSON only. This is not
a production soak.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from apps.cashbook.models import CashSession
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.common.endurance_characterization import (
    COVERAGE,
    DURATION_SECONDS,
    KIND,
    PACE_SECONDS,
    PLAN_OPERATIONS,
    PROFILE_NAME,
    PROOF_FLAGS,
    PROVISIONAL_P95_MS,
    REGISTER_CODE,
    WORKERS,
    assert_endurance_path,
    assess_endurance,
    assess_isolation,
    build_plan,
    caddy_metrics_observation,
    classify_status,
    expected_writes,
    judge,
    loop_queue_cap,
    make_key,
    members_required,
    metrics_delta,
    pace_delay,
    resolve_profile,
    same_gym_link_mismatches,
    schedule_match,
    summarize_resources,
    summarize_scenario,
    throughput,
)
from apps.common.load_characterization import (
    assert_metrics_target,
    assert_rehearsal_context,
    marker_present,
    material_leaked,
    percentile_ms,
)
from apps.expenses.models import Expense
from apps.members.models import CheckIn, Member
from apps.members.services import create_member, create_membership, create_plan, gym_today
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    LoadError,
    Peak,
    _json_body,
    _login_ok,
    _nested_id,
    _refresh_csrf,
    _safe_text,
    _sample_postgres,
)
from apps.tenants.models import ExpenseCategory, GymMembership
from apps.tenants.services import (
    _create_pending_owner_invitation,
    accept_owner_invitation,
    provision_gym,
)

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
    ("ENDGYMA", "Endurance Gym A", "endurance-a@rehearsal.invalid"),
    ("ENDGYMB", "Endurance Gym B", "endurance-b@rehearsal.invalid"),
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class EnduranceError(RuntimeError):
    """The characterization failed closed without a secret in the message."""


@dataclass
class GymState:
    index: int
    gym_id: UUID
    email: str
    password: str
    jar: Jar
    member_ids: list[str]
    category_id: str
    business_date: str
    expense_id: str = ""
    cash_id: str = ""
    expense_body: bytes = b""
    cash_body: bytes = b""
    checkin_body: bytes = b""
    checkin_key: str = ""


@dataclass
class Pacer:
    nxt: dict[int, float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, worker: int, deadline: float) -> bool:
        now = time.monotonic()
        with self.lock:
            earliest = self.nxt.get(worker, now)
            delay = pace_delay(earliest=float(earliest), now=float(now), deadline=float(deadline))
            if delay is None:
                return False
            start = now if earliest <= now else earliest
            self.nxt[worker] = float(start) + PACE_SECONDS
        if delay > 0:
            time.sleep(delay)
        return time.monotonic() < deadline


def _install_owner(
    gym_id: UUID,
    membership: GymMembership,
    email: str,
    password: str,
) -> GymMembership:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
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
            request_id="endurance-seed",
            ip_address=None,
            user_agent="endurance",
        )
    finally:
        del token


def _seed(plan: Sequence[Mapping[str, object]]) -> list[GymState]:
    needed = members_required(plan)
    states: list[GymState] = []
    for index, (code, name, email) in enumerate(_GYMS):
        password = secrets.token_urlsafe(24)
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=email)
            membership = _install_owner(
                provisioned.gym.id,
                provisioned.owner_membership,
                email,
                password,
            )
            today = gym_today(provisioned.gym)
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                category = ExpenseCategory.objects.get(gym=provisioned.gym, code="RENT")
                plan_row = create_plan(
                    gym=provisioned.gym,
                    actor=membership,
                    code="END30",
                    name="Endurance Plan",
                    description="",
                    duration_value=30,
                    duration_unit="DAY",
                    price=Decimal("0.00"),
                )
                member_ids: list[str] = []
                for _ordinal in range(needed[index]):
                    member = create_member(
                        gym=provisioned.gym,
                        actor=membership,
                        member_number="",
                        first_name="Endurance",
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
                        actor=membership,
                        member=member,
                        plan=plan_row,
                        start_date=today,
                        discount_amount=Decimal("0.00"),
                        note="",
                    )
                    member_ids.append(str(member.id))
                clear_gym_context()
        except Exception as exc:
            raise EnduranceError(f"staging endurance seed failed: {_safe_text(exc)}") from None
        states.append(
            GymState(
                index=index,
                gym_id=provisioned.gym.id,
                email=email,
                password=password,
                jar=Jar(),
                member_ids=member_ids,
                category_id=str(category.id),
                business_date=today.isoformat(),
            )
        )
    connection.close()
    return states


def _snapshot(states: Sequence[GymState]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for state in states:
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            owner = str(state.gym_id)
            checkins = list(CheckIn.objects.values_list("member_id", flat=True))
            expenses = list(Expense.objects.values_list("category_id", flat=True))
            cash = list(CashSession.objects.values_list("opened_by_membership_id", flat=True))
            members = {str(item) for item in Member.objects.values_list("id", flat=True)}
            categories = {
                str(item) for item in ExpenseCategory.objects.values_list("id", flat=True)
            }
            memberships = {str(item) for item in GymMembership.objects.values_list("id", flat=True)}
            clear_gym_context()
        links: list[tuple[str, str | None]] = []
        for member_id in checkins:
            links.append((owner, owner if str(member_id) in members else None))
        for category_id in expenses:
            links.append((owner, owner if str(category_id) in categories else None))
        for opener in cash:
            if opener is None:
                links.append((owner, None))
            else:
                links.append((owner, owner if str(opener) in memberships else None))
        rows.append(
            {
                "gym": state.index,
                "checkins": len(checkins),
                "expenses": len(expenses),
                "cash_sessions": len(cash),
                "gym_mismatch": same_gym_link_mismatches(links),
            }
        )
    connection.close()
    return rows


def _http_5xx(status: object) -> bool:
    return type(status) is int and status >= 500


def _as_int(value: object) -> int:
    if type(value) is not int:
        raise EnduranceError("staging endurance count is invalid")
    return value


def _deltas(
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    prior: dict[int, Mapping[str, object]] = {}
    for row in before:
        gym = row.get("gym")
        if type(gym) is int:
            prior[gym] = row
    found: list[dict[str, object]] = []
    for row in after:
        gym = row.get("gym")
        if type(gym) is not int or gym not in prior:
            continue
        old = prior[gym]
        found.append(
            {
                "gym": gym,
                "checkins": _as_int(row.get("checkins")) - _as_int(old.get("checkins")),
                "expenses": _as_int(row.get("expenses")) - _as_int(old.get("expenses")),
                "cash_sessions": _as_int(row.get("cash_sessions"))
                - _as_int(old.get("cash_sessions")),
                "gym_mismatch": row.get("gym_mismatch"),
            }
        )
    return found


def _optional_counter(path: str) -> int | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    return int(text)


def _cpu_usage_usec() -> int | None:
    try:
        text = Path("/sys/fs/cgroup/cpu.stat").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        name, separator, raw = line.partition(" ")
        if separator and name == "usage_usec" and raw.isdigit():
            return int(raw)
    return None


def _resource_reading() -> dict[str, object]:
    memory = _optional_counter("/sys/fs/cgroup/memory.current")
    if memory is None:
        memory = _optional_counter("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    try:
        disk: int | None = shutil.disk_usage("/").free
    except OSError:
        disk = None
    return {"memory_bytes": memory, "cpu_usage_usec": _cpu_usage_usec(), "disk_free_bytes": disk}


def _metrics_observation() -> tuple[bool, int | None]:
    try:
        ips = _resolve_metrics()
        conn = http.client.HTTPConnection(ips[0], 2020, timeout=3)
        try:
            conn.request("GET", "/metrics", headers={"Host": "caddy", "Connection": "close"})
            response = conn.getresponse()
            if response.status != 200:
                return False, None
            text = response.read(1_000_000).decode("utf-8", "replace")
        finally:
            conn.close()
    except (OSError, LoadError, ValueError, EnduranceError):
        return False, None
    return caddy_metrics_observation(text)


def _resolve_metrics() -> list[str]:
    import socket

    found: list[str] = []
    for info in socket.getaddrinfo("caddy", 2020, type=socket.SOCK_STREAM):
        address = str(info[4][0])
        if address not in found:
            found.append(address)
    assert_metrics_target("caddy", 2020, found)
    return found


class Runner:
    def __init__(
        self,
        client: Client,
        states: Sequence[GymState],
        passwords: Sequence[str],
    ) -> None:
        self.client = client
        self.states = list(states)
        self.passwords = list(passwords)
        self.outcomes: list[dict[str, object]] = []
        self.lock = threading.Lock()
        self.peak = Peak()

    def record(self, outcome: Mapping[str, object]) -> None:
        with self.lock:
            self.outcomes.append(dict(outcome))

    def run_step(self, step: Mapping[str, object]) -> dict[str, object]:
        self.peak.enter()
        started = time.perf_counter()
        try:
            scenario, status, _body, leak, credential = self._perform(step)
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.peak.exit()
        return {
            "scenario": scenario,
            "phase": str(step.get("phase", "")),
            "gym": step.get("gym"),
            "ordinal": step.get("ordinal"),
            "slot": step.get("slot"),
            "transport": "http",
            "status": status,
            "classification": classify_status(scenario, status),
            "latency_ms": max(0, elapsed),
            "leak": leak,
            "credential": credential,
        }

    def _state(self, index: object) -> GymState:
        if type(index) is not int or index not in (0, 1):
            raise EnduranceError("staging endurance gym selector is invalid")
        return self.states[index]

    def _perform(self, step: Mapping[str, object]) -> tuple[str, object, bytes, bool, bool]:
        scenario = str(step["scenario"])
        state = self._state(step.get("gym"))
        ordinal = step.get("ordinal")
        if type(ordinal) is not int:
            raise EnduranceError("staging endurance ordinal is invalid")
        if scenario == "auth_login":
            return self._login(state)
        return self._authenticated(scenario, state, ordinal, step.get("slot"))

    def _login(self, state: GymState) -> tuple[str, object, bytes, bool, bool]:
        if len(state.jar.cookies.get("gms_csrf", "")) < 32:
            _refresh_csrf(self.client, state.jar)
        body = _json_body(
            {"email": state.email, "password": state.password, "device_name": "endurance-client"}
        )
        status, payload, leak, credential = self._call(
            "auth_login",
            "POST",
            "/api/auth/login",
            jar=state.jar,
            body=body,
            csrf=True,
            markers=[*self.passwords, state.password],
        )
        if not _login_ok(status, payload, state.jar.cookies):
            status = status if status != 200 else "login_rejected"
        return "auth_login", status, payload, leak, credential

    def _call(
        self,
        scenario: str,
        method: str,
        path: str,
        *,
        jar: Jar | None,
        body: bytes | None = None,
        csrf: bool = False,
        idempotency: str | None = None,
        markers: Sequence[str],
        other_markers: Sequence[str] = (),
    ) -> tuple[object, bytes, bool, bool]:
        assert_endurance_path(method, path)
        status, payload, cookies = self.client.exchange(
            method,
            path,
            jar=jar,
            body=body,
            csrf=csrf,
            idempotency=idempotency,
        )
        if jar is not None and cookies and scenario == "auth_login":
            jar.cookies.update(cookies)
        passwords = [item.encode("utf-8") for item in markers if len(item) >= 12]
        text = payload.decode("utf-8", "replace")
        credential = marker_present(payload, passwords) or _JWT.search(text) is not None
        leak = status in {200, 201} and marker_present(
            payload, [item.encode("utf-8") for item in other_markers if len(item) >= 8]
        )
        return status, payload, leak, credential

    def _authenticated(
        self,
        scenario: str,
        state: GymState,
        ordinal: int,
        slot: object,
    ) -> tuple[str, object, bytes, bool, bool]:
        other = self.states[1 - state.index]
        other_markers = [other.email, *other.member_ids]
        if other.expense_id:
            other_markers.append(other.expense_id)
        if other.cash_id:
            other_markers.append(other.cash_id)
        markers = [*self.passwords, state.password]
        date = state.business_date
        if scenario == "dashboard_read":
            return self._done(
                scenario,
                "GET",
                "/api/operations/dashboard?range=today",
                state,
                markers,
                other_markers,
            )
        if scenario == "report_read":
            return self._done(
                scenario,
                "GET",
                f"/api/operations/reports/daily?date={date}",
                state,
                markers,
                other_markers,
            )
        if scenario == "pt_report_read":
            return self._done(
                scenario,
                "GET",
                f"/api/operations/reports/pt-sessions?date_from={date}&date_to={date}",
                state,
                markers,
                other_markers,
            )
        if scenario == "checkin_list":
            return self._done(
                scenario,
                "GET",
                "/api/operations/check-ins?limit=5",
                state,
                markers,
                other_markers,
            )
        if scenario == "cash_read":
            return self._done(
                scenario,
                "GET",
                "/api/operations/cash-sessions?limit=5",
                state,
                markers,
                other_markers,
            )
        if scenario == "expense_read":
            if not state.expense_id:
                return scenario, "unavailable", b"", False, False
            return self._done(
                scenario,
                "GET",
                f"/api/operations/expenses/{state.expense_id}",
                state,
                markers,
                other_markers,
            )
        if scenario == "cross_gym_expense":
            if not other.expense_id:
                return scenario, "unavailable", b"", False, False
            return self._done(
                scenario,
                "GET",
                f"/api/operations/expenses/{other.expense_id}",
                state,
                markers,
                other_markers,
            )
        if scenario == "cross_gym_cash":
            if not other.cash_id:
                return scenario, "unavailable", b"", False, False
            return self._done(
                scenario,
                "GET",
                f"/api/operations/cash-sessions/{other.cash_id}",
                state,
                markers,
                other_markers,
            )
        if scenario == "cross_gym_checkin":
            if not other.member_ids:
                return scenario, "unavailable", b"", False, False
            body = _json_body({"member_id": other.member_ids[0], "note": "Endurance cross"})
            return self._write(
                scenario,
                state,
                "/api/operations/check-ins",
                body,
                make_key("cross-checkin", state.index, ordinal),
                markers,
                other_markers,
            )
        if scenario in {"cash_open", "cash_replay"}:
            if scenario == "cash_open":
                state.cash_body = _json_body(
                    {
                        "register_code": REGISTER_CODE,
                        "business_date": date,
                        "opening_cash": "0.00",
                    }
                )
            status, payload, leak, credential = self._write(
                scenario,
                state,
                "/api/operations/cash-sessions/open",
                state.cash_body,
                make_key("cash-open", state.index, 0),
                markers,
                other_markers,
            )[1:]
            if scenario == "cash_open" and status == 201:
                state.cash_id = _nested_id(payload, ("data", "session", "id"))
                if not state.cash_id:
                    return scenario, "unavailable", b"", leak, credential
            return scenario, status, payload, leak, credential
        if scenario in {"expense_setup", "expense_replay", "expense_create"}:
            expense_ordinal = 0 if scenario != "expense_create" else ordinal
            if scenario == "expense_create":
                body = _json_body(
                    {
                        "category_id": state.category_id,
                        "business_date": date,
                        "description": "Endurance draft",
                        "amount": "1.00",
                        "currency": "THB",
                        "payment_method": "CASH",
                    }
                )
            else:
                if scenario == "expense_setup":
                    state.expense_body = _json_body(
                        {
                            "category_id": state.category_id,
                            "business_date": date,
                            "description": "Endurance draft",
                            "amount": "1.00",
                            "currency": "THB",
                            "payment_method": "CASH",
                        }
                    )
                body = state.expense_body
            status, payload, leak, credential = self._write(
                scenario,
                state,
                "/api/operations/expenses",
                body,
                make_key("expense", state.index, expense_ordinal),
                markers,
                other_markers,
            )[1:]
            if scenario == "expense_setup" and status == 201:
                state.expense_id = _nested_id(payload, ("data", "id"))
                if not state.expense_id:
                    return scenario, "unavailable", b"", leak, credential
            return scenario, status, payload, leak, credential
        if scenario in {"checkin_create", "checkin_replay"}:
            if scenario == "checkin_replay":
                if not state.checkin_key or not state.checkin_body:
                    return scenario, "unavailable", b"", False, False
                key = state.checkin_key
                body = state.checkin_body
            else:
                if type(slot) is not int or slot >= len(state.member_ids):
                    return scenario, "unavailable", b"", False, False
                key = make_key("checkin", state.index, ordinal)
                body = _json_body({"member_id": state.member_ids[slot], "note": "Endurance visit"})
                if not state.checkin_key:
                    state.checkin_key = key
                    state.checkin_body = body
            status, payload, leak, credential = self._write(
                scenario,
                state,
                "/api/operations/check-ins",
                body,
                key,
                markers,
                other_markers,
            )[1:]
            return scenario, status, payload, leak, credential
        return scenario, "unavailable", b"", False, False

    def _done(
        self,
        scenario: str,
        method: str,
        path: str,
        state: GymState,
        markers: Sequence[str],
        other_markers: Sequence[str],
    ) -> tuple[str, object, bytes, bool, bool]:
        status, payload, leak, credential = self._call(
            scenario,
            method,
            path,
            jar=state.jar,
            markers=markers,
            other_markers=other_markers,
        )
        return scenario, status, payload, leak, credential

    def _write(
        self,
        scenario: str,
        state: GymState,
        path: str,
        body: bytes,
        key: str,
        markers: Sequence[str],
        other_markers: Sequence[str],
    ) -> tuple[str, object, bytes, bool, bool]:
        status, payload, leak, credential = self._call(
            scenario,
            "POST",
            path,
            jar=state.jar,
            body=body,
            csrf=True,
            idempotency=key,
            markers=markers,
            other_markers=other_markers,
        )
        return scenario, status, payload, leak, credential


def _blank(states: Sequence[GymState]) -> None:
    for state in states:
        state.password = ""
        state.email = ""
        state.jar.cookies.clear()
        state.member_ids.clear()
        state.category_id = ""
        state.expense_id = ""
        state.cash_id = ""
        state.checkin_key = ""
        state.checkin_body = b""
        state.expense_body = b""
        state.cash_body = b""


def _reconciliation(
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]],
    *,
    matched: bool,
) -> dict[str, object]:
    gyms: list[dict[str, object]] = []
    for row in snapshots:
        gym = row.get("gym")
        if type(gym) is not int:
            continue
        expected = expected_writes(build_plan(), gym)
        gyms.append(
            {
                "gym": gym,
                "checkins": row.get("checkins"),
                "expenses": row.get("expenses"),
                "cash_sessions": row.get("cash_sessions"),
                "expected_checkins": expected["checkins"],
                "expected_expenses": expected["expenses"],
                "expected_cash_sessions": expected["cash_sessions"],
                "gym_mismatch": row.get("gym_mismatch"),
            }
        )
    return {"transport": "service-only", "matched": matched, "gyms": gyms}


def _finish(
    plan: Sequence[Mapping[str, object]],
    outcomes: list[dict[str, object]],
    snapshots: list[dict[str, object]] | None,
    *,
    cancelled: int,
    wall: float,
    measured: float,
    peak_workers: int,
    postgres_peak: int | None,
    postgres_max: int | None,
    caddy_before: tuple[bool, int | None],
    caddy_after: tuple[bool, int | None],
    resources: Sequence[Mapping[str, object]],
    stage: str,
    detail: str,
) -> dict[str, Any]:
    financial, matched, corruption = assess_endurance(
        plan, outcomes, snapshots, cancelled=cancelled
    )
    isolation = assess_isolation(plan, outcomes) if snapshots is not None else "not-run"
    observed, delta = metrics_delta(caddy_before, caddy_after)
    latencies: list[int] = []
    for item in outcomes:
        latency = item.get("latency_ms")
        if type(latency) is int and item.get("classification") == "success":
            latencies.append(latency)
    http_complete = cancelled == 0 and schedule_match(plan, outcomes)
    phase = measured if measured > 0 else 0.0
    report: dict[str, Any] = {
        "kind": KIND,
        "profile": PROFILE_NAME,
        "evidence_name": "endurance.json",
        "local_endurance_status": "pass" if financial == "pass" and isolation == "pass" else "fail",
        "local_isolation_status": isolation,
        "local_financial_reconciliation_status": financial,
        "local_soak_status": "not-run",
        "thresholds_are_approved_slos": False,
        "duration_seconds": DURATION_SECONDS,
        "pace_seconds": PACE_SECONDS,
        "workers": WORKERS,
        "planned_operations": PLAN_OPERATIONS,
        "completed_operations": len(outcomes),
        "cancelled_operations": cancelled,
        "measured_phase_seconds": round(measured, 3),
        "wall_time_seconds": round(wall, 3),
        "throughput_per_second": throughput(len(outcomes), round(wall, 3) if wall > 0 else 0.0),
        "peak_active_workers": peak_workers,
        "cross_gym_leakage": sum(1 for item in outcomes if item.get("leak") is True),
        "credential_exposure": sum(1 for item in outcomes if item.get("credential") is True),
        "unexpected_errors": sum(
            1 for item in outcomes if item.get("classification") == "unexpected"
        ),
        "api_unexpected_5xx": sum(1 for item in outcomes if _http_5xx(item.get("status"))),
        "caddy_metrics_observed": observed,
        "caddy_5xx_delta": delta,
        "reconciliation_matched": matched,
        "data_corruption": corruption,
        "postgres_connection_peak": postgres_peak,
        "postgres_max_connections": postgres_max,
        "required_complete": http_complete and financial == "pass",
        "http_p95_ms": percentile_ms(latencies, 95) if latencies else None,
        "provisional_local_p95_ceiling_ms": PROVISIONAL_P95_MS,
        "architecture_note": (
            "Measured minutes on a 4 GiB Docker VM are a local bounded endurance "
            "characterization. They are not a production soak. The production "
            "planning target is 8 GiB. Ceilings are not an approved SLO."
        ),
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [
            summarize_scenario(name, outcomes, phase_seconds=phase)
            for name in RULE_ORDER
            if any(item.get("scenario") == name for item in outcomes)
        ],
        "outcomes": outcomes,
        "reconciliation": _reconciliation(outcomes, snapshots or [], matched=matched),
        "failure_stage": stage,
        "failure_detail": detail,
        **summarize_resources(resources),
    }
    for flag in PROOF_FLAGS:
        report[flag] = False
    status, reasons = judge(report)
    report["local_endurance_status"] = status
    report["gate_reasons"] = reasons
    return report


RULE_ORDER = (
    "auth_login",
    "cash_open",
    "cash_replay",
    "expense_setup",
    "expense_replay",
    "expense_read",
    "checkin_create",
    "checkin_replay",
    "dashboard_read",
    "report_read",
    "pt_report_read",
    "checkin_list",
    "expense_create",
    "cash_read",
    "cross_gym_checkin",
    "cross_gym_expense",
    "cross_gym_cash",
)


def run_characterization(cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise EnduranceError(
            "staging endurance characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_ENDURANCE_PROJECT") != _REQUIRED_PROJECT:
        raise EnduranceError("staging endurance characterization requires the disposable project")
    for flag in _REFUSED_LIVE_PROOF:
        if os.environ.get(flag) == "1":
            raise EnduranceError("staging endurance characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_ENDURANCE_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"]["HOST"]),
        database_user=str(settings.DATABASES["default"]["USER"]),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise EnduranceError("staging endurance characterization requires the rehearsal flag")
    profile = resolve_profile(PROFILE_NAME)
    if os.environ.get("STAGING_ENDURANCE_PROFILE") != profile.name:
        raise EnduranceError("staging endurance profile was not the fixed rehearsal profile")
    plan = build_plan()
    states = _seed(plan)
    passwords = [state.password for state in states]
    sensitive = [
        *passwords,
        *(state.email for state in states),
        "ENDGYMA",
        "ENDGYMB",
        "Endurance Gym A",
        "Endurance Gym B",
    ]
    try:
        before = _snapshot(states)
        client = Client(cafile, profile.timeout_seconds)
        runner = Runner(client, states, passwords)
        fatal = False
        caddy_before = _metrics_observation()
        pg_samples: list[tuple[int, int]] = []
        resource_samples: list[dict[str, object]] = []
        stop = threading.Event()

        def sample() -> None:
            try:
                while not stop.is_set():
                    try:
                        pg_samples.append(_sample_postgres())
                    except (DatabaseError, OSError, LoadError, ValueError):
                        pg_samples.append((-1, -1))
                    resource_samples.append(_resource_reading())
                    stop.wait(2.0)
            finally:
                connection.close()

        sampler = threading.Thread(target=sample, name="endurance-sample")
        sampler.start()
        started = time.perf_counter()
        deadline = time.monotonic() + profile.duration_seconds
        try:
            setup = [step for step in plan if step.get("phase") == "setup"]
            loop = [step for step in plan if step.get("phase") == "loop"]
            loop_queue_cap(len(loop))
            for step in setup:
                if time.monotonic() >= deadline:
                    fatal = True
                    break
                outcome = runner.run_step(step)
                runner.record(outcome)
                if outcome["classification"] != "success":
                    fatal = True
                    break
            measured_started = time.perf_counter()
            if not fatal:
                work: queue.Queue[Mapping[str, object]] = queue.Queue(maxsize=len(loop))
                for step in loop:
                    work.put(step)
                pacer = Pacer()

                def worker(index: int) -> None:
                    while True:
                        if time.monotonic() >= deadline:
                            return
                        try:
                            step = work.get_nowait()
                        except queue.Empty:
                            return
                        if not pacer.wait(index, deadline):
                            work.task_done()
                            return
                        runner.record(runner.run_step(step))
                        work.task_done()

                threads = [
                    threading.Thread(target=worker, args=(index,), name=f"endurance-worker-{index}")
                    for index in range(profile.workers)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(profile.duration_seconds + profile.timeout_seconds)
            measured = time.perf_counter() - measured_started
            done = {
                (item.get("phase"), item.get("scenario"), item.get("gym"), item.get("ordinal"))
                for item in runner.outcomes
            }
            cancelled = 0
            for step in plan:
                identity = (
                    step.get("phase"),
                    step.get("scenario"),
                    step.get("gym"),
                    step.get("ordinal"),
                )
                if identity not in done:
                    cancelled += 1
            wall = time.perf_counter() - started
            caddy_after = _metrics_observation()
            stop.set()
            sampler.join(5)
            usable = [item for item in pg_samples if item[0] >= 0 and item[1] > 0]
            postgres_peak = max((item[0] for item in usable), default=None)
            postgres_max = usable[-1][1] if usable else None
            after = _deltas(before, _snapshot(states))
            report = _finish(
                plan,
                runner.outcomes,
                after,
                cancelled=cancelled,
                wall=wall,
                measured=measured,
                peak_workers=runner.peak.peak,
                postgres_peak=postgres_peak,
                postgres_max=postgres_max,
                caddy_before=caddy_before,
                caddy_after=caddy_after,
                resources=resource_samples,
                stage="",
                detail="",
            )
            for state in states:
                sensitive.extend(
                    (state.expense_id, state.cash_id, state.category_id, str(state.gym_id))
                )
            encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if material_leaked(encoded, sensitive):
                raise EnduranceError("staging endurance report failed redaction")
            return report
        finally:
            stop.set()
            sampler.join(5)
    finally:
        _blank(states)


def characterize(cafile: Path) -> dict[str, Any]:
    try:
        return run_characterization(cafile)
    except Exception as exc:
        report = {
            "kind": KIND,
            "profile": PROFILE_NAME,
            "local_endurance_status": "fail",
            "local_isolation_status": "not-run",
            "local_financial_reconciliation_status": "not-run",
            "local_soak_status": "not-run",
            "failure_stage": "characterization",
            "failure_detail": _safe_text(exc),
            "gate_reasons": ["characterization"],
            "load_test_proven": False,
            "soak_test_proven": False,
            "production_capacity_proven": False,
            "slo_proven": False,
            "financial_reconciliation_proven": False,
        }
        return report


class Command(BaseCommand):
    help = "Run the fixed local endurance characterization. Not a production soak."

    def handle(self, *args: Any, **options: Any) -> None:
        cafile = Path(os.environ.get("STAGING_ENDURANCE_CA_FILE", ""))
        report = characterize(cafile)
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(
            rendered,
            (
                "ENDGYMA",
                "ENDGYMB",
                "endurance-a@rehearsal.invalid",
                "endurance-b@rehearsal.invalid",
            ),
        ):
            report = {
                "kind": KIND,
                "profile": PROFILE_NAME,
                "local_endurance_status": "fail",
                "local_soak_status": "not-run",
                "failure_stage": "redaction",
                "failure_detail": "redaction",
                "soak_test_proven": False,
                "load_test_proven": False,
                "production_capacity_proven": False,
                "slo_proven": False,
                "financial_reconciliation_proven": False,
            }
            rendered = json.dumps(report, sort_keys=True)
        self.stdout.write(rendered)
        if report.get("local_endurance_status") != "pass":
            raise CommandError("local endurance characterization failed")

"""Bounded HTTP load characterization for the disposable staging rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds two synthetic gyms, keeps passwords and cookies in memory, and sends
fixed requests through Caddy. It prints metric JSON only.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import secrets
import socket
import ssl
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
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from apps.cashbook.models import CashSession
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.common.load_characterization import (
    AUTH_THROTTLE_NOTE,
    COVERAGE,
    MAX_RESPONSE_BYTES,
    PROVISIONAL_P95_MS,
    assert_metrics_target,
    assert_rehearsal_context,
    assert_request_target,
    assess_financial,
    build_plan,
    caddy_5xx_total,
    classify_status,
    clip_body,
    connection_within_ceiling,
    deadline_action,
    judge,
    make_idempotency_key,
    marker_present,
    material_leaked,
    members_required,
    percentile_ms,
    redirect_follow,
    resolve_profile,
    scenarios_match,
    summarize_scenario,
)
from apps.expenses.models import Expense
from apps.members.models import CheckIn
from apps.members.services import create_member, create_membership, create_plan, gym_today
from apps.tenants.models import ExpenseCategory, GymMembership
from apps.tenants.services import (
    _create_pending_owner_invitation,
    accept_owner_invitation,
    provision_gym,
)

_UNKNOWN_EMAIL = "load-unknown@rehearsal.invalid"
_UNKNOWN_PASSWORD = "load-wrong-password-not-used"
_REQUIRED_SETTINGS = "config.settings.staging_rehearsal"
_REQUIRED_PROJECT = "gym-staging-rehearsal"
_REFUSED_LIVE_PROOF = (
    "load_test_proven",
    "soak_test_proven",
    "production_capacity_proven",
    "slo_proven",
)
_GYMS = (
    ("LOADGYMA", "Load Gym A", "load-gym-a@rehearsal.invalid"),
    ("LOADGYMB", "Load Gym B", "load-gym-b@rehearsal.invalid"),
)
_COOKIE_NAMES = frozenset({"gms_access", "gms_refresh", "gms_csrf"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_HEX = frozenset("0123456789abcdef")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class LoadError(RuntimeError):
    """The characterization failed closed without a secret in the message."""


@dataclass
class Jar:
    cookies: dict[str, str] = field(default_factory=dict)

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())


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
    lock: threading.Lock = field(default_factory=threading.Lock)
    checkin_ordinal: int = 0
    expense_ordinal: int = 1


@dataclass
class Peak:
    lock: threading.Lock = field(default_factory=threading.Lock)
    current: int = 0
    peak: int = 0

    def enter(self) -> None:
        with self.lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self) -> None:
        with self.lock:
            self.current = max(0, self.current - 1)


def _safe_text(exc: BaseException) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "@" in detail or "password" in lowered or "token" in lowered or len(detail) > 180:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}"


def _ssl_context(cafile: Path) -> ssl.SSLContext:
    if cafile.is_symlink() or not cafile.is_file() or cafile.stat().st_size > 65536:
        raise LoadError("staging load tls trust is missing")
    text = cafile.read_text(encoding="utf-8")
    if "PRIVATE KEY" in text or "BEGIN CERTIFICATE" not in text:
        raise LoadError("staging load tls trust is missing")
    context = ssl.create_default_context(cafile=str(cafile))
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise LoadError("staging load tls verification is disabled")
    return context


def _resolve(host: str, port: int) -> list[str]:
    found: list[str] = []
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = str(info[4][0])
        if address not in found:
            found.append(address)
    return found


def _take_cookies(headers: Sequence[tuple[str, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in headers:
        if key.lower() != "set-cookie":
            continue
        pair = value.split(";", 1)[0]
        name, separator, cookie = pair.partition("=")
        if separator and name in _COOKIE_NAMES and cookie:
            found[name] = cookie
    return found


def _looks_like_uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        return ""
    lowered = value.lower()
    parts = lowered.split("-")
    if [len(part) for part in parts] != [8, 4, 4, 4, 12]:
        return ""
    if any(character not in _HEX for part in parts for character in part):
        return ""
    return lowered


def _nested_id(payload: bytes, path: tuple[str, ...]) -> str:
    try:
        current: object = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    for part in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return _looks_like_uuid(current)


class Client:
    def __init__(self, cafile: Path, timeout: float) -> None:
        self.timeout = timeout
        ips = _resolve("caddy", 443)
        assert_request_target("caddy", 443, "localhost", ips)
        self._ip = ips[0]
        self._ssl = _ssl_context(cafile)

    def exchange(
        self,
        method: str,
        path: str,
        *,
        jar: Jar | None,
        body: bytes | None = None,
        csrf: bool = False,
        idempotency: str | None = None,
        redirects: int = 0,
    ) -> tuple[int | str, bytes, dict[str, str]]:
        started = time.perf_counter()
        try:
            status, payload, cookies, location = self._once(
                method,
                path,
                jar=jar,
                body=body,
                csrf=csrf,
                idempotency=idempotency,
            )
        except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException):
            return "timeout", b"", {}
        if status in _REDIRECTS:
            decision = redirect_follow(
                status=status,
                location=location if isinstance(location, str) else "",
                method=method,
                redirects_followed=redirects,
            )
            if decision is None:
                return "redirect_rejected", b"", cookies
            follow_method = str(decision["method"])
            preserve = decision["preserve_body"] is True
            return self.exchange(
                follow_method,
                str(decision["target"]),
                jar=jar,
                body=body if preserve else None,
                csrf=csrf if decision["preserve_csrf"] is True else False,
                idempotency=idempotency if decision["preserve_idempotency"] is True else None,
                redirects=redirects + 1,
            )
        _ = started
        return status, payload, cookies

    def _once(
        self,
        method: str,
        path: str,
        *,
        jar: Jar | None,
        body: bytes | None,
        csrf: bool,
        idempotency: str | None,
    ) -> tuple[int, bytes, dict[str, str], str | None]:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        tls = self._ssl.wrap_socket(sock, server_hostname="localhost")
        conn = http.client.HTTPSConnection(
            "localhost", 443, context=self._ssl, timeout=self.timeout
        )
        conn.sock = tls
        headers = {
            "Host": "localhost",
            "Accept": "application/json,text/html",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Origin": "https://localhost:8443",
        }
        if jar is not None and jar.cookies:
            headers["Cookie"] = jar.header()
        if csrf:
            token = "" if jar is None else jar.cookies.get("gms_csrf", "")
            if len(token) < 32:
                conn.close()
                raise OSError("csrf")
            headers["x-csrf-token"] = token
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            status = int(response.status)
            location = response.getheader("Location")
            cookies = _take_cookies(response.getheaders())
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                while response.read(8192):
                    pass
            clipped, _truncated = clip_body(payload)
            return status, clipped, cookies, location
        finally:
            conn.close()


def _metrics_5xx() -> int | None:
    try:
        ips = _resolve("caddy", 2020)
        assert_metrics_target("caddy", 2020, ips)
        conn = http.client.HTTPConnection(ips[0], 2020, timeout=3)
        try:
            conn.request("GET", "/metrics", headers={"Host": "caddy", "Connection": "close"})
            response = conn.getresponse()
            if response.status != 200:
                return None
            text = response.read(1_000_000).decode("utf-8", "replace")
        finally:
            conn.close()
    except (OSError, LoadError, ValueError):
        return None
    return caddy_5xx_total(text)


def _sample_postgres() -> tuple[int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT numbackends FROM pg_stat_database WHERE datname = current_database()"
        )
        backends = cursor.fetchone()
        cursor.execute("SELECT setting FROM pg_settings WHERE name = 'max_connections'")
        maximum = cursor.fetchone()
    if backends is None or maximum is None:
        raise LoadError("staging load postgres sample failed")
    return int(backends[0]), int(maximum[0])


def _install_owner(
    gym_id: UUID, membership: GymMembership, email: str, password: str
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
            request_id="load-seed",
            ip_address=None,
            user_agent="load",
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
                provisioned.gym.id, provisioned.owner_membership, email, password
            )
            today = gym_today(provisioned.gym)
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                category = ExpenseCategory.objects.get(gym=provisioned.gym, code="RENT")
                plan_row = create_plan(
                    gym=provisioned.gym,
                    actor=membership,
                    code="LOAD30",
                    name="Load Plan",
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
            raise LoadError(f"staging load seed failed: {_safe_text(exc)}") from None
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


def _counts(states: Sequence[GymState]) -> dict[int, dict[str, int]]:
    found: dict[int, dict[str, int]] = {}
    for state in states:
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            expenses = Expense.objects.count()
            checkins = CheckIn.objects.count()
            cash = CashSession.objects.count()
            clear_gym_context()
        found[state.index] = {"expenses": expenses, "checkins": checkins, "cash": cash}
    connection.close()
    return found


def _json_body(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _refresh_csrf(client: Client, jar: Jar) -> None:
    status, _body, cookies = client.exchange("GET", "/api/auth/csrf", jar=jar)
    jar.cookies.update(cookies)
    if status != 200 or len(jar.cookies.get("gms_csrf", "")) < 32:
        raise LoadError("staging load csrf bootstrap failed")


def _login_ok(status: object, body: bytes, cookies: Mapping[str, str]) -> bool:
    if status != 200 or "gms_access" not in cookies:
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return True
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, dict):
        return True
    return data.get("requires_mfa") is not True and data.get("requires_gym_selection") is not True


class Runner:
    def __init__(
        self, client: Client, states: Sequence[GymState], passwords: Sequence[str]
    ) -> None:
        self.client = client
        self.states = list(states)
        self.passwords = list(passwords)
        self.anonymous = Jar()
        self.outcomes: list[dict[str, object]] = []
        self.lock = threading.Lock()
        self.peak = Peak()

    def run_step(self, step: Mapping[str, object]) -> dict[str, object]:
        self.peak.enter()
        started = time.perf_counter()
        try:
            scenario, status, body, leak, credential = self._perform(step)
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.peak.exit()
        classification = classify_status(scenario, status)
        if scenario == "public_login_page" and classification == "success":
            lowered = body.lower()
            if b"<html" not in lowered and b"<!doctype html" not in lowered:
                classification = "unexpected"
        if scenario == "staff_api_blocked" and body.strip() != b"Not found":
            classification = "unexpected"
        if scenario == "auth_login" and classification == "success" and status != 200:
            classification = "unexpected"
        return {
            "scenario": scenario,
            "phase": str(step.get("phase", "")),
            "transport": "http",
            "status": status,
            "classification": classification,
            "latency_ms": max(0, elapsed),
            "leak": leak,
            "credential": credential,
        }

    def record(self, outcome: Mapping[str, object]) -> None:
        with self.lock:
            self.outcomes.append(dict(outcome))

    def _state(self, index: object) -> GymState:
        if type(index) is not int or index not in (0, 1):
            raise LoadError("staging load gym selector is invalid")
        return self.states[index]

    def _other(self, state: GymState) -> GymState:
        return self.states[1 - state.index]

    def _perform(self, step: Mapping[str, object]) -> tuple[str, object, bytes, bool, bool]:
        scenario = str(step["scenario"])
        if scenario == "public_login_page":
            return self._call(scenario, "GET", "/login", jar=None, markers=self.passwords)
        if scenario == "staff_api_blocked":
            return self._call(
                scenario,
                "POST",
                "/api/v1/auth/login/",
                jar=None,
                body=b"{}",
                markers=self.passwords,
            )
        if scenario == "member_auth_failure":
            body = _json_body({"email": _UNKNOWN_EMAIL, "password": _UNKNOWN_PASSWORD})
            return self._call(
                scenario,
                "POST",
                "/api/v1/member-auth/login/",
                jar=None,
                body=body,
                markers=[*self.passwords, _UNKNOWN_PASSWORD],
            )
        if scenario == "auth_failure":
            if len(self.anonymous.cookies.get("gms_csrf", "")) < 32:
                _refresh_csrf(self.client, self.anonymous)
            body = _json_body(
                {
                    "email": _UNKNOWN_EMAIL,
                    "password": _UNKNOWN_PASSWORD,
                    "device_name": "load-client",
                }
            )
            return self._call(
                scenario,
                "POST",
                "/api/auth/login",
                jar=self.anonymous,
                body=body,
                csrf=True,
                markers=[*self.passwords, _UNKNOWN_PASSWORD],
            )
        state = self._state(step.get("gym"))
        if scenario == "auth_login":
            if len(state.jar.cookies.get("gms_csrf", "")) < 32:
                _refresh_csrf(self.client, state.jar)
            body = _json_body(
                {"email": state.email, "password": state.password, "device_name": "load-client"}
            )
            status, payload, leak, credential = self._call(
                scenario,
                "POST",
                "/api/auth/login",
                jar=state.jar,
                body=body,
                csrf=True,
                markers=[*self.passwords, state.password],
            )[1:]
            # _call returns scenario too; unpack carefully below.
            return scenario, status, payload, leak, credential
        ordinal = step.get("ordinal", 0)
        if type(ordinal) is not int:
            raise LoadError("staging load ordinal is invalid")
        return self._authenticated(scenario, state, ordinal)

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
    ) -> tuple[str, object, bytes, bool, bool]:
        status, payload, cookies = self.client.exchange(
            method,
            path,
            jar=jar,
            body=body,
            csrf=csrf,
            idempotency=idempotency,
        )
        if jar is not None and cookies and scenario in {"auth_login", "auth_failure"}:
            jar.cookies.update(cookies)
        if scenario == "auth_login" and not _login_ok(
            status, payload, jar.cookies if jar is not None else {}
        ):
            status = status if status != 200 else "login_rejected"
        passwords = [item.encode("utf-8") for item in markers if len(item) >= 12]
        text = payload.decode("utf-8", "replace")
        credential = marker_present(payload, passwords)
        if scenario != "public_login_page" and _JWT.search(text) is not None:
            credential = True
        leak = status in {200, 201} and marker_present(
            payload, [item.encode("utf-8") for item in other_markers if len(item) >= 8]
        )
        return scenario, status, payload, leak, credential

    def _authenticated(
        self, scenario: str, state: GymState, ordinal: int
    ) -> tuple[str, object, bytes, bool, bool]:
        other = self._other(state)
        other_markers = [other.email, *other.member_ids]
        if other.expense_id:
            other_markers.append(other.expense_id)
        if other.cash_id:
            other_markers.append(other.cash_id)
        markers = [*self.passwords, state.password]
        if scenario == "dashboard_read":
            return self._call(
                scenario,
                "GET",
                "/api/operations/dashboard?range=today",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "report_read":
            return self._call(
                scenario,
                "GET",
                f"/api/operations/reports/daily?date={state.business_date}",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "checkin_list":
            return self._call(
                scenario,
                "GET",
                "/api/operations/check-ins?limit=5",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "cash_read":
            return self._call(
                scenario,
                "GET",
                "/api/operations/cash-sessions?limit=5",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "expense_read":
            if not state.expense_id:
                return scenario, "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/expenses/{state.expense_id}",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "cross_gym_expense":
            if not other.expense_id:
                return scenario, "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/expenses/{other.expense_id}",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "cross_gym_cash":
            if not other.cash_id:
                return scenario, "unavailable", b"", False, False
            return self._call(
                scenario,
                "GET",
                f"/api/operations/cash-sessions/{other.cash_id}",
                jar=state.jar,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario == "cross_gym_checkin":
            if not other.member_ids:
                return scenario, "unavailable", b"", False, False
            key = make_idempotency_key("cross-checkin", state.index, ordinal)
            body = _json_body({"member_id": other.member_ids[0], "note": "Load cross"})
            return self._call(
                scenario,
                "POST",
                "/api/operations/check-ins",
                jar=state.jar,
                body=body,
                csrf=True,
                idempotency=key,
                markers=markers,
                other_markers=other_markers,
            )
        if scenario in {"cash_open", "cash_replay"}:
            if scenario == "cash_open":
                state.cash_body = _json_body(
                    {
                        "register_code": "LOAD",
                        "business_date": state.business_date,
                        "opening_cash": "0.00",
                    }
                )
            key = make_idempotency_key("cash-open", state.index, 0)
            status, payload, leak, credential = self._call(
                scenario,
                "POST",
                "/api/operations/cash-sessions/open",
                jar=state.jar,
                body=state.cash_body,
                csrf=True,
                idempotency=key,
                markers=markers,
                other_markers=other_markers,
            )[1:]
            if scenario == "cash_open" and status == 201:
                state.cash_id = _nested_id(payload, ("data", "session", "id"))
                if not state.cash_id:
                    return scenario, "unavailable", b"", leak, credential
            return scenario, status, payload, leak, credential
        if scenario in {"expense_setup", "expense_replay", "expense_create"}:
            if scenario == "expense_create":
                with state.lock:
                    expense_ordinal = state.expense_ordinal
                    state.expense_ordinal += 1
                key = make_idempotency_key("expense", state.index, expense_ordinal)
                body = _json_body(
                    {
                        "category_id": state.category_id,
                        "business_date": state.business_date,
                        "description": "Load draft",
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
                            "business_date": state.business_date,
                            "description": "Load draft",
                            "amount": "1.00",
                            "currency": "THB",
                            "payment_method": "CASH",
                        }
                    )
                key = make_idempotency_key("expense", state.index, 0)
                body = state.expense_body
            actual = scenario
            status, payload, leak, credential = self._call(
                actual,
                "POST",
                "/api/operations/expenses",
                jar=state.jar,
                body=body,
                csrf=True,
                idempotency=key,
                markers=markers,
                other_markers=other_markers,
            )[1:]
            if scenario == "expense_setup" and status == 201:
                state.expense_id = _nested_id(payload, ("data", "id"))
                if not state.expense_id:
                    return actual, "unavailable", b"", leak, credential
            return actual, status, payload, leak, credential
        if scenario in {"checkin_create", "checkin_duplicate", "checkin_replay"}:
            if scenario == "checkin_replay":
                if not state.checkin_key or not state.checkin_body:
                    return scenario, "unavailable", b"", False, False
                key = state.checkin_key
                body = state.checkin_body
                actual = scenario
            else:
                with state.lock:
                    slot = state.checkin_ordinal
                    state.checkin_ordinal += 1
                    if slot < len(state.member_ids):
                        actual = "checkin_create"
                        member_id = state.member_ids[slot]
                    else:
                        actual = "checkin_duplicate"
                        member_id = state.member_ids[0]
                    key = make_idempotency_key("checkin", state.index, slot)
                    body = _json_body({"member_id": member_id, "note": "Load visit"})
                    if actual == "checkin_create" and not state.checkin_key:
                        state.checkin_key = key
                        state.checkin_body = body
            status, payload, leak, credential = self._call(
                actual,
                "POST",
                "/api/operations/check-ins",
                jar=state.jar,
                body=body,
                csrf=True,
                idempotency=key,
                markers=markers,
                other_markers=other_markers,
            )[1:]
            return actual, status, payload, leak, credential
        return scenario, "unavailable", b"", False, False


def _base_report(profile: str) -> dict[str, Any]:
    return {
        "kind": "local-disposable-load-characterization",
        "profile": profile,
        "transport_note": "Scenario rows are HTTP. Reconciliation is service-only.",
        "local_load_status": "fail",
        "local_isolation_status": "not-run",
        "local_financial_reconciliation_status": "not-run",
        "thresholds_are_approved_slos": False,
        "auth_throttle_note": AUTH_THROTTLE_NOTE,
        "provisional_local_p95_ceiling_ms": PROVISIONAL_P95_MS,
        "architecture_note": (
            "Local arm64 or amd64-emulated ClamAV on a 4 GiB Docker VM cannot "
            "validate the 8 GiB production planning budget. These ceilings are "
            "not an approved SLO."
        ),
        "cross_gym_leakage": 0,
        "credential_exposure": 0,
        "unexpected_errors": 0,
        "api_unexpected_5xx": 0,
        "caddy_5xx_delta": None,
        "reconciliation_matched": False,
        "data_corruption": False,
        "postgres_connection_peak": None,
        "postgres_max_connections": None,
        "auth_rate_limit_observed": False,
        "cancelled_operations": 0,
        "required_complete": False,
        "http_p95_ms": None,
        "wall_time_seconds": 0,
        "peak_active_workers": 0,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False},
        "failure_stage": "",
        "failure_detail": "",
        "load_test_proven": False,
        "soak_test_proven": False,
        "production_capacity_proven": False,
        "slo_proven": False,
    }


def _finish(
    profile: str,
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    *,
    started: float,
    peak_workers: int,
    postgres_peak: int | None,
    postgres_max: int | None,
    caddy_before: int | None,
    caddy_after: int | None,
    before: Mapping[int, Mapping[str, int]],
    after: Mapping[int, Mapping[str, int]],
    cancelled: int,
    required_complete: bool,
    stage: str,
    detail: str,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for item in outcomes:
        grouped[str(item["scenario"])].append(item)
    scenarios = [
        summarize_scenario(name, rows, elapsed_seconds=max(time.perf_counter() - started, 0.001))
        for name, rows in sorted(grouped.items())
    ]
    latencies = [
        latency
        for item in outcomes
        if item.get("transport") == "http"
        and item.get("classification") == "success"
        and isinstance((latency := item.get("latency_ms")), int)
        and not isinstance(latency, bool)
    ]
    leakage = sum(1 for item in outcomes if item.get("leak") is True)
    exposure = sum(1 for item in outcomes if item.get("credential") is True)
    unexpected = sum(1 for item in outcomes if item.get("classification") == "unexpected")
    api_5xx = sum(
        1
        for item in outcomes
        if isinstance((status := item.get("status")), int)
        and not isinstance(status, bool)
        and status >= 500
    )
    rate_limited = any(
        item.get("scenario") == "auth_failure" and item.get("status") == 429 for item in outcomes
    )
    creates = _success_count(outcomes, "expense_setup") + _success_count(outcomes, "expense_create")
    checkins = _success_count(outcomes, "checkin_create")
    cash = _success_count(outcomes, "cash_open")
    observed = {
        "expenses": _delta(before, after, "expenses"),
        "checkins": _delta(before, after, "checkins"),
        "cash": _delta(before, after, "cash"),
    }
    expected = {"expenses": creates, "checkins": checkins, "cash": cash}
    financial_status, matched, corruption = assess_financial(
        plan, outcomes, observed if before and after else None
    )
    report = _base_report(profile)
    report.update(
        {
            "failure_stage": stage,
            "failure_detail": detail,
            "scenarios": scenarios,
            "cross_gym_leakage": leakage,
            "credential_exposure": exposure,
            "unexpected_errors": unexpected,
            "api_unexpected_5xx": api_5xx,
            "caddy_5xx_delta": None
            if caddy_before is None or caddy_after is None
            else caddy_after - caddy_before,
            "reconciliation_matched": matched,
            "data_corruption": corruption,
            "postgres_connection_peak": postgres_peak,
            "postgres_max_connections": postgres_max,
            "auth_rate_limit_observed": rate_limited,
            "cancelled_operations": cancelled,
            "required_complete": required_complete,
            "http_p95_ms": percentile_ms(latencies, 95),
            "wall_time_seconds": round(max(0.0, time.perf_counter() - started), 3),
            "peak_active_workers": peak_workers,
            "local_isolation_status": (
                "pass" if leakage == 0 and _cross_probes(outcomes) else "fail"
            ),
            "local_financial_reconciliation_status": financial_status,
            "reconciliation": {
                "transport": "service-only",
                "expense_draft_delta": observed["expenses"],
                "expense_draft_expected": expected["expenses"],
                "check_in_delta": observed["checkins"],
                "check_in_expected": expected["checkins"],
                "cash_session_delta": observed["cash"],
                "cash_session_expected": expected["cash"],
                "matched": matched,
            },
        }
    )
    status, reasons = judge(report)
    report["local_load_status"] = status
    report["gate_reasons"] = reasons
    if not connection_within_ceiling(postgres_peak, postgres_max):
        report["connection_ceiling"] = "fail"
    else:
        report["connection_ceiling"] = "pass"
    return report


def _success_count(outcomes: Sequence[Mapping[str, object]], scenario: str) -> int:
    return sum(
        1
        for item in outcomes
        if item.get("scenario") == scenario and item.get("classification") == "success"
    )


def _cross_probes(outcomes: Sequence[Mapping[str, object]]) -> bool:
    needed = {"cross_gym_checkin", "cross_gym_expense", "cross_gym_cash"}
    seen = {
        str(item.get("scenario"))
        for item in outcomes
        if item.get("classification") == "expected_rejection"
    }
    return needed <= seen


def _delta(
    before: Mapping[int, Mapping[str, int]],
    after: Mapping[int, Mapping[str, int]],
    key: str,
) -> int:
    return sum(row.get(key, 0) for row in after.values()) - sum(
        row.get(key, 0) for row in before.values()
    )


def _blank_secrets(states: Sequence[GymState]) -> None:
    for state in states:
        state.password = ""
        state.email = ""
        state.jar.cookies.clear()
        state.member_ids.clear()
        state.category_id = ""
        state.expense_id = ""
        state.cash_id = ""


def run_characterization(profile_name: str, cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise LoadError("staging load characterization requires the staging rehearsal settings")
    if os.environ.get("STAGING_LOAD_PROJECT") != _REQUIRED_PROJECT:
        raise LoadError("staging load characterization requires the disposable project")
    for flag in _REFUSED_LIVE_PROOF:
        if os.environ.get(flag) == "1":
            raise LoadError("staging load characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_LOAD_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"]["HOST"]),
        database_user=str(settings.DATABASES["default"]["USER"]),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise LoadError("staging load characterization requires the rehearsal flag")
    profile = resolve_profile(profile_name)
    if os.environ.get("STAGING_LOAD_PROFILE") != profile.name:
        raise LoadError("staging load profile was not the fixed rehearsal profile")
    plan = build_plan(profile.name)
    started = time.perf_counter()
    deadline = time.monotonic() + profile.duration_seconds
    states = _seed(plan)
    passwords = [state.password for state in states]
    sensitive = [*passwords]
    sensitive.extend(state.email for state in states)
    sensitive.extend(
        (_UNKNOWN_EMAIL, _UNKNOWN_PASSWORD, "LOADGYMA", "LOADGYMB", "Load Gym A", "Load Gym B")
    )
    sensitive.extend(member for state in states for member in state.member_ids)
    before = _counts(states)
    client = Client(cafile, profile.timeout_seconds)
    runner = Runner(client, states, passwords)
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
        except (DatabaseError, OSError, LoadError, ValueError):
            samples.append((-1, -1))
        finally:
            connection.close()

    sampler = threading.Thread(target=sample, name="load-postgres")
    sampler.start()
    try:
        for step in plan:
            if step.get("phase") != "fixed":
                continue
            if time.monotonic() >= deadline:
                cancelled += 1
                fatal = True
                continue
            outcome = runner.run_step(step)
            runner.record(outcome)
            if step.get("scenario") == "auth_login" and outcome["classification"] != "success":
                fatal = True
                break
        if not fatal:
            loop = [step for step in plan if step.get("phase") == "loop"]
            work: queue.Queue[Mapping[str, object]] = queue.Queue(maxsize=max(1, len(loop)))
            for step in loop:
                work.put(step)

            def worker() -> None:
                nonlocal cancelled
                while True:
                    if time.monotonic() >= deadline:
                        return
                    try:
                        step = work.get_nowait()
                    except queue.Empty:
                        return
                    if (
                        deadline_action(deadline=deadline, now=time.monotonic(), pending=1)
                        == "cancel"
                    ):
                        with runner.lock:
                            cancelled += 1
                        work.task_done()
                        return
                    runner.record(runner.run_step(step))
                    work.task_done()

            threads = [
                threading.Thread(target=worker, name=f"load-worker-{index}")
                for index in range(profile.workers)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(profile.timeout_seconds + profile.duration_seconds)
            while not work.empty():
                try:
                    work.get_nowait()
                except queue.Empty:
                    break
                cancelled += 1
            if all(not thread.is_alive() for thread in threads):
                for step in plan:
                    if step.get("phase") == "final":
                        runner.record(runner.run_step(step))
            else:
                fatal = True
        caddy_after = _metrics_5xx()
        stop.set()
        sampler.join(5)
        postgres_peak, postgres_max = _postgres_peak(samples)
        after = _counts(states)
        required_complete = cancelled == 0 and not fatal and scenarios_match(plan, runner.outcomes)
        report = _finish(
            profile.name,
            plan,
            runner.outcomes,
            started=started,
            peak_workers=runner.peak.peak,
            postgres_peak=postgres_peak,
            postgres_max=postgres_max,
            caddy_before=caddy_before,
            caddy_after=caddy_after,
            before=before,
            after=after,
            cancelled=cancelled,
            required_complete=required_complete,
            stage="http",
            detail="",
        )
        for state in states:
            sensitive.extend(
                (
                    state.expense_id,
                    state.cash_id,
                    state.category_id,
                    str(state.gym_id),
                    state.checkin_key,
                )
            )
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if material_leaked(encoded, sensitive):
            raise LoadError("staging load report failed redaction")
        return report
    finally:
        stop.set()
        sampler.join(5)
        _blank_secrets(states)


def _postgres_peak(samples: Sequence[tuple[int, int]]) -> tuple[int | None, int | None]:
    usable = [item for item in samples if item[0] >= 0 and item[1] > 0]
    if not usable:
        return None, None
    return max(item[0] for item in usable), usable[-1][1]


def characterize(profile_name: str, cafile: Path) -> dict[str, Any]:
    try:
        return run_characterization(profile_name, cafile)
    except Exception as exc:  # fail closed without a secret traceback
        report = _base_report(profile_name if profile_name in {"short", "bounded"} else "short")
        report["failure_stage"] = "characterization"
        report["failure_detail"] = _safe_text(exc)
        report["local_load_status"] = "fail"
        report["gate_reasons"] = ["characterization"]
        return report


class Command(BaseCommand):
    help = "Run the fixed staging load characterization. Not a production proof."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--profile", choices=("short", "bounded"), required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        profile = str(options["profile"])
        cafile = Path(os.environ.get("STAGING_LOAD_CA_FILE", ""))
        report = characterize(profile, cafile)
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(rendered, (_UNKNOWN_EMAIL, _UNKNOWN_PASSWORD, "LOADGYMA", "LOADGYMB")):
            report = _base_report(profile)
            report["failure_stage"] = "redaction"
            report["failure_detail"] = "redaction"
            rendered = json.dumps(report, sort_keys=True)
        self.stdout.write(rendered)
        if report.get("local_load_status") != "pass":
            raise CommandError("staging load characterization failed")

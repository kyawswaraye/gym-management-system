"""Pure rules for the disposable local endurance characterization.

This module does not open sockets, import Django, or read credentials. The
staging management command and the rehearsal harness apply these rules. A
local pass is a bounded endurance measurement on the rehearsal stack. It is
not a production soak, load proof, capacity proof, or approved SLO.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.common.load_characterization import (
    MAX_RESPONSE_BYTES,
    PROOF_FLAGS,
    PROVISIONAL_P95_MS,
    caddy_metrics_observation,
    clip_body,
    connection_within_ceiling,
    material_leaked,
    percentile_ms,
)
from apps.common.pt_characterization import same_gym_link_mismatches

SCHEMA = "gym-staging-endurance/v1"
PROJECT = "gym-staging-rehearsal"
KIND = "local-disposable-endurance-characterization"
PROFILE_NAME = "endurance"
EVIDENCE_NAME = "endurance.json"
SETTINGS_MODULE = "config.settings.staging_rehearsal"
DURATION_SECONDS = 180
WORKERS = 2
PACE_SECONDS = 2.5
TIMEOUT_SECONDS = 8.0
REPEATS = 5
MIN_MEASURED_PHASE_SECONDS = 120.0
PLAN_OPERATIONS = 126
LOOP_OPERATIONS = 110
REGISTER_CODE = "ENDUR"

SETUP: tuple[str, ...] = (
    "auth_login",
    "cash_open",
    "cash_replay",
    "expense_setup",
    "expense_replay",
    "expense_read",
    "checkin_create",
    "checkin_replay",
)
CYCLE: tuple[str, ...] = (
    "dashboard_read",
    "report_read",
    "pt_report_read",
    "checkin_list",
    "checkin_create",
    "expense_create",
    "expense_read",
    "cash_read",
    "cross_gym_checkin",
    "cross_gym_expense",
    "cross_gym_cash",
)
_WRITE_SCENARIOS = frozenset({"checkin_create", "expense_setup", "expense_create", "cash_open"})
_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}\Z")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_COUNT_KEYS = ("checkins", "expenses", "cash_sessions")
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
_OUTCOME_KEYS = (
    "scenario",
    "phase",
    "gym",
    "ordinal",
    "slot",
    "transport",
    "status",
    "classification",
    "latency_ms",
    "leak",
    "credential",
)


class EnduranceSpecError(ValueError):
    """A fixed profile or evidence document violated a local rule."""


@dataclass(frozen=True)
class ScenarioRule:
    success: frozenset[int]
    expected_rejection: frozenset[int]


RULES: dict[str, ScenarioRule] = {
    "auth_login": ScenarioRule(frozenset({200}), frozenset()),
    "dashboard_read": ScenarioRule(frozenset({200}), frozenset()),
    "report_read": ScenarioRule(frozenset({200}), frozenset()),
    "pt_report_read": ScenarioRule(frozenset({200}), frozenset()),
    "checkin_list": ScenarioRule(frozenset({200}), frozenset()),
    "checkin_create": ScenarioRule(frozenset({201}), frozenset()),
    "checkin_replay": ScenarioRule(frozenset({200}), frozenset()),
    "expense_setup": ScenarioRule(frozenset({201}), frozenset()),
    "expense_create": ScenarioRule(frozenset({201}), frozenset()),
    "expense_replay": ScenarioRule(frozenset({200, 201}), frozenset()),
    "expense_read": ScenarioRule(frozenset({200}), frozenset()),
    "cash_open": ScenarioRule(frozenset({201}), frozenset()),
    "cash_replay": ScenarioRule(frozenset({200, 201}), frozenset()),
    "cash_read": ScenarioRule(frozenset({200}), frozenset()),
    "cross_gym_checkin": ScenarioRule(frozenset(), frozenset({404})),
    "cross_gym_expense": ScenarioRule(frozenset(), frozenset({404})),
    "cross_gym_cash": ScenarioRule(frozenset(), frozenset({404})),
}

COVERAGE: tuple[dict[str, object], ...] = (
    {
        "id": "dashboard_read",
        "method": "GET",
        "route": "/api/operations/dashboard",
        "transport": "http",
        "covered": True,
        "note": "Repeated authenticated owner dashboard read through the BFF.",
    },
    {
        "id": "daily_report_read",
        "method": "GET",
        "route": "/api/operations/reports/daily",
        "transport": "http",
        "covered": True,
        "note": "Repeated authenticated daily report read through the BFF.",
    },
    {
        "id": "pt_report_read",
        "method": "GET",
        "route": "/api/operations/reports/pt-sessions",
        "transport": "http",
        "covered": True,
        "note": "Repeated authenticated PT report read. This profile does not create a PT sale.",
    },
    {
        "id": "checkin",
        "method": "POST",
        "route": "/api/operations/check-ins",
        "transport": "http",
        "covered": True,
        "note": "Unique check-in creates, one idempotent replay, and repeated list reads.",
    },
    {
        "id": "expense_draft",
        "method": "POST",
        "route": "/api/operations/expenses",
        "transport": "http",
        "covered": True,
        "note": (
            "Unique draft creates, one idempotent replay, and repeated reads. "
            "Drafts are not posted."
        ),
    },
    {
        "id": "cash_read",
        "method": "GET",
        "route": "/api/operations/cash-sessions",
        "transport": "http",
        "covered": True,
        "note": "One cash open and replay per gym, then repeated reads. The till is not closed.",
    },
    {
        "id": "cross_gym",
        "method": "GET",
        "route": "/api/operations/expenses/{id}",
        "transport": "http",
        "covered": True,
        "note": "Cross-gym check-in, expense, and cash probes run in every loop repeat.",
    },
    {
        "id": "auth_failure",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "uncovered",
        "covered": False,
        "note": "Authentication failure sampling is omitted so it cannot throttle owner logins.",
    },
    {
        "id": "cash_close_or_payout",
        "method": "",
        "route": "cash close or commission payout",
        "transport": "uncovered",
        "covered": False,
        "note": "Immutable close and payout operations are not repeated.",
    },
    {
        "id": "production_soak",
        "method": "",
        "route": "production soak",
        "transport": "uncovered",
        "covered": False,
        "note": "Minutes on a 4 GiB VM are not a production soak.",
    },
)


def validate_profile(profile: Profile) -> None:
    if profile.name != PROFILE_NAME:
        raise EnduranceSpecError("endurance profile is not the fixed profile")
    if type(profile.duration_seconds) is not int or profile.duration_seconds != DURATION_SECONDS:
        raise EnduranceSpecError("duration exceeds the staging cap")
    if type(profile.workers) is not int or profile.workers != WORKERS:
        raise EnduranceSpecError("worker count exceeds the staging cap")
    if type(profile.pace_seconds) is not float or profile.pace_seconds != PACE_SECONDS:
        raise EnduranceSpecError("pacing exceeds the staging cap")
    if type(profile.timeout_seconds) is not float or profile.timeout_seconds != TIMEOUT_SECONDS:
        raise EnduranceSpecError("timeout exceeds the staging cap")
    if type(profile.repeats) is not int or profile.repeats != REPEATS:
        raise EnduranceSpecError("repeat count exceeds the staging cap")


@dataclass(frozen=True)
class Profile:
    name: str
    duration_seconds: int
    workers: int
    pace_seconds: float
    timeout_seconds: float
    repeats: int

    def bounds(self) -> None:
        validate_profile(self)


def resolve_profile(name: str) -> Profile:
    if name != PROFILE_NAME:
        raise EnduranceSpecError("endurance profile is not the fixed profile")
    profile = Profile(
        PROFILE_NAME,
        DURATION_SECONDS,
        WORKERS,
        PACE_SECONDS,
        TIMEOUT_SECONDS,
        REPEATS,
    )
    profile.bounds()
    return profile


def operation_cap(count: int) -> int:
    if type(count) is not int or count != PLAN_OPERATIONS:
        raise EnduranceSpecError("operation count exceeds the staging cap")
    return count


def loop_queue_cap(count: int) -> int:
    if type(count) is not int or count != LOOP_OPERATIONS:
        raise EnduranceSpecError("operation count exceeds the staging cap")
    return count


def _step(
    scenario: str,
    gym: int,
    ordinal: int,
    *,
    phase: str,
    slot: int | None,
) -> dict[str, object]:
    if scenario not in RULES or phase not in {"setup", "loop"}:
        raise EnduranceSpecError("scenario is not in the fixed catalog")
    if type(gym) is not int or gym not in (0, 1):
        raise EnduranceSpecError("gym selector is outside the profile")
    if type(ordinal) is not int or ordinal < 0:
        raise EnduranceSpecError("ordinal is outside the profile")
    if slot is not None and (type(slot) is not int or slot < 0):
        raise EnduranceSpecError("member slot is outside the profile")
    return {
        "scenario": scenario,
        "gym": gym,
        "ordinal": ordinal,
        "slot": slot,
        "required": True,
        "phase": phase,
    }


def build_plan(name: str = PROFILE_NAME) -> list[dict[str, object]]:
    profile = resolve_profile(name)
    steps: list[dict[str, object]] = []
    for scenario in SETUP:
        for gym in (0, 1):
            slot = 0 if scenario == "checkin_create" else None
            steps.append(_step(scenario, gym, 0, phase="setup", slot=slot))
    for repeat in range(profile.repeats):
        for scenario in CYCLE:
            for gym in (0, 1):
                if scenario == "checkin_create":
                    ordinal = repeat + 1
                    slot = repeat + 1
                elif scenario == "expense_create":
                    ordinal = repeat + 1
                    slot = None
                elif scenario == "cross_gym_checkin":
                    ordinal = repeat * 2 + gym
                    slot = None
                else:
                    ordinal = repeat
                    slot = None
                steps.append(_step(scenario, gym, ordinal, phase="loop", slot=slot))
    operation_cap(len(steps))
    loop_queue_cap(sum(1 for step in steps if step["phase"] == "loop"))
    return steps


def members_required(plan: Sequence[Mapping[str, object]]) -> tuple[int, int]:
    counts = [0, 0]
    for step in plan:
        if step.get("scenario") != "checkin_create":
            continue
        gym = step.get("gym")
        slot = step.get("slot")
        if type(gym) is not int or gym not in (0, 1) or type(slot) is not int:
            raise EnduranceSpecError("member slot is outside the profile")
        counts[gym] = max(counts[gym], slot + 1)
    if counts[0] < 1 or counts[1] < 1:
        raise EnduranceSpecError("member slot is outside the profile")
    return counts[0], counts[1]


def make_key(kind: str, gym: int, ordinal: int) -> str:
    if kind not in {"cash-open", "expense", "checkin", "cross-checkin"}:
        raise EnduranceSpecError("idempotency key kind is not fixed")
    if type(gym) is not int or gym not in (0, 1):
        raise EnduranceSpecError("idempotency key gym is outside the profile")
    if type(ordinal) is not int or ordinal < 0 or ordinal > 10_000:
        raise EnduranceSpecError("idempotency key ordinal is outside the profile")
    key = f"end-{kind}-g{gym}-{ordinal:04d}"
    if _KEY.fullmatch(key) is None:
        raise EnduranceSpecError("idempotency key is invalid")
    return key


def assert_endurance_path(method: str, path: str) -> None:
    if type(method) is not str or method not in {"GET", "POST"}:
        raise EnduranceSpecError("request method is outside the allowlist")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise EnduranceSpecError("request target is outside the allowlist")
    route = parsed.path
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    allowed_query = dict(query)
    if len(allowed_query) != len(query):
        raise EnduranceSpecError("request target is outside the allowlist")
    if method == "GET" and route == "/api/auth/csrf" and not allowed_query:
        return
    if method == "POST" and route == "/api/auth/login" and not allowed_query:
        return
    if (
        method == "GET"
        and route == "/api/operations/dashboard"
        and allowed_query == {"range": "today"}
    ):
        return
    if (
        method == "GET"
        and route == "/api/operations/reports/daily"
        and set(allowed_query) == {"date"}
        and _DATE.fullmatch(allowed_query["date"]) is not None
    ):
        return
    if (
        method == "GET"
        and route == "/api/operations/reports/pt-sessions"
        and set(allowed_query) == {"date_from", "date_to"}
        and allowed_query["date_from"] == allowed_query["date_to"]
        and _DATE.fullmatch(allowed_query["date_from"]) is not None
    ):
        return
    if method == "GET" and route == "/api/operations/check-ins" and allowed_query == {"limit": "5"}:
        return
    if method == "POST" and route == "/api/operations/check-ins" and not allowed_query:
        return
    if method == "POST" and route == "/api/operations/expenses" and not allowed_query:
        return
    if method == "GET" and route.startswith("/api/operations/expenses/"):
        identity = route.removeprefix("/api/operations/expenses/")
        if not allowed_query and _UUID.fullmatch(identity) is not None:
            return
    if method == "POST" and route == "/api/operations/cash-sessions/open" and not allowed_query:
        return
    if (
        method == "GET"
        and route == "/api/operations/cash-sessions"
        and allowed_query == {"limit": "5"}
    ):
        return
    if method == "GET" and route.startswith("/api/operations/cash-sessions/"):
        identity = route.removeprefix("/api/operations/cash-sessions/")
        if identity != "open" and not allowed_query and _UUID.fullmatch(identity) is not None:
            return
    raise EnduranceSpecError("request target is outside the allowlist")


def classify_status(scenario: str, status: object) -> str:
    rule = RULES.get(scenario)
    if rule is None or isinstance(status, bool) or not isinstance(status, int):
        return "unexpected"
    if status >= 500:
        return "unexpected"
    if status in rule.success:
        return "success"
    if status in rule.expected_rejection:
        return "expected_rejection"
    return "unexpected"


def pace_delay(*, earliest: float, now: float, deadline: float) -> float | None:
    """Return the wait before the next start, or None when the deadline wins.

    A boolean clock is rejected. Returning None cancels the operation. It does
    not start the request late.
    """

    if type(earliest) is not float or type(now) is not float or type(deadline) is not float:
        raise EnduranceSpecError("pacing clock is invalid")
    if now >= deadline:
        return None
    delay = earliest - now
    if delay < 0:
        delay = 0.0
    if now + delay >= deadline:
        return None
    return delay


def schedule_match(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> bool:
    """Return whether outcomes are the planned scenario, gym, and ordinal multiset.

    Completion order may differ across the two workers. The schedule identity
    is the multiset, which ``build_plan`` fixes.
    """

    def identity(item: Mapping[str, object]) -> tuple[object, ...] | None:
        phase = item.get("phase")
        scenario = item.get("scenario")
        gym = item.get("gym")
        ordinal = item.get("ordinal")
        if not isinstance(phase, str) or not isinstance(scenario, str):
            return None
        if type(gym) is not int or type(ordinal) is not int:
            return None
        return phase, scenario, gym, ordinal

    planned = [identity(step) for step in plan]
    actual = [identity(item) for item in outcomes]
    if any(item is None for item in planned) or any(item is None for item in actual):
        return False
    return Counter(planned) == Counter(actual)


def _successes(outcomes: Sequence[Mapping[str, object]], scenario: str, gym: int) -> int:
    return sum(
        1
        for item in outcomes
        if item.get("scenario") == scenario
        and item.get("gym") == gym
        and item.get("classification") == "success"
    )


def _snapshot_counts(row: Mapping[str, object]) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for key in _COUNT_KEYS:
        value = row.get(key)
        if type(value) is not int or value < 0:
            return None
        found[key] = value
    mismatch = row.get("gym_mismatch")
    if type(mismatch) is not int or mismatch < 0:
        return None
    found["gym_mismatch"] = mismatch
    return found


def _writes_match(outcomes: Sequence[Mapping[str, object]], row: Mapping[str, object]) -> bool:
    counts = _snapshot_counts(row)
    gym = row.get("gym")
    if counts is None or type(gym) is not int or gym not in (0, 1):
        return False
    expected = {
        "checkins": _successes(outcomes, "checkin_create", gym),
        "expenses": _successes(outcomes, "expense_setup", gym)
        + _successes(outcomes, "expense_create", gym),
        "cash_sessions": _successes(outcomes, "cash_open", gym),
    }
    return all(counts[key] == value for key, value in expected.items())


def _zero_writes(
    outcomes: Sequence[Mapping[str, object]],
    rows: Sequence[Mapping[str, object]],
) -> bool:
    write_seen = any(
        _successes(outcomes, scenario, gym) != 0 for scenario in _WRITE_SCENARIOS for gym in (0, 1)
    )
    if write_seen:
        return False
    for row in rows:
        counts = _snapshot_counts(row)
        if counts is None or any(counts[key] != 0 for key in (*_COUNT_KEYS, "gym_mismatch")):
            return False
    return True


def _relation_broken(rows: Sequence[Mapping[str, object]]) -> bool:
    for row in rows:
        mismatch = row.get("gym_mismatch")
        if type(mismatch) is not int or mismatch > 0:
            return True
    return False


def assess_endurance(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    *,
    cancelled: int,
) -> tuple[str, bool, bool]:
    """Return financial status, reconciliation match, and data_corruption.

    Reconciliation compares each gym's stored rows with the unique writes that
    actually succeeded. An unfinished plan that still matches those writes is
    fail with data_corruption false. data_corruption is true when the stored
    rows disagree or a same-gym relation is already broken. An early all-zero
    stop is not-run.
    """

    if type(cancelled) is not int or cancelled < 0:
        raise EnduranceSpecError("cancelled count is invalid")
    if snapshots is None:
        return "not-run", False, False
    if len(snapshots) != 2:
        return "fail", False, True
    http_complete = (
        cancelled == 0
        and schedule_match(plan, outcomes)
        and all(
            item.get("classification") in {"success", "expected_rejection"} for item in outcomes
        )
    )
    matched_writes = all(_writes_match(outcomes, row) for row in snapshots)
    relation_broken = _relation_broken(snapshots)
    vacuous = _zero_writes(outcomes, snapshots)
    exercised = all(
        _successes(outcomes, "checkin_create", gym) >= 1
        and _successes(outcomes, "expense_setup", gym) + _successes(outcomes, "expense_create", gym)
        >= 1
        and _successes(outcomes, "cash_open", gym) >= 1
        for gym in (0, 1)
    )
    if http_complete and matched_writes and exercised and not relation_broken:
        return "pass", True, False
    if not http_complete and vacuous and not relation_broken:
        return "not-run", False, False
    if not http_complete:
        return "fail", False, (not matched_writes) or relation_broken
    return "fail", False, (not matched_writes) or relation_broken


def assess_isolation(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> str:
    cross_plan = [step for step in plan if str(step.get("scenario", "")).startswith("cross_gym_")]
    cross_done = [
        item for item in outcomes if str(item.get("scenario", "")).startswith("cross_gym_")
    ]
    leaked = any(item.get("leak") is True for item in outcomes)
    unexpected = any(item.get("classification") != "expected_rejection" for item in cross_done)
    if leaked or unexpected:
        return "fail"
    if len(cross_done) != len(cross_plan) or not schedule_match(plan, outcomes):
        return "not-run"
    return "pass"


def expected_writes(plan: Sequence[Mapping[str, object]], gym: int) -> dict[str, int]:
    if type(gym) is not int or gym not in (0, 1):
        raise EnduranceSpecError("gym selector is outside the profile")
    return {
        "checkins": sum(
            1
            for step in plan
            if step.get("scenario") == "checkin_create" and step.get("gym") == gym
        ),
        "expenses": sum(
            1
            for step in plan
            if step.get("scenario") in {"expense_setup", "expense_create"}
            and step.get("gym") == gym
        ),
        "cash_sessions": sum(
            1 for step in plan if step.get("scenario") == "cash_open" and step.get("gym") == gym
        ),
    }


def throughput(count: int, seconds: float) -> float | None:
    if type(count) is not int or count < 0:
        raise EnduranceSpecError("throughput count is invalid")
    if type(seconds) is not float or seconds < 0:
        raise EnduranceSpecError("throughput duration is invalid")
    if seconds == 0:
        return None
    return round(count / seconds, 3)


def summarize_resources(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Merge local container readings. A missing reading stays null."""

    def ints(key: str) -> list[int]:
        found: list[int] = []
        for sample in samples:
            value = sample.get(key)
            if value is None:
                continue
            if type(value) is not int or value < 0:
                raise EnduranceSpecError("resource sample is invalid")
            found.append(value)
        return found

    memory = ints("memory_bytes")
    cpu = ints("cpu_usage_usec")
    disk = ints("disk_free_bytes")
    cpu_value: int | None
    if len(cpu) >= 2:
        delta = cpu[-1] - cpu[0]
        cpu_value = delta if delta >= 0 else None
    elif len(cpu) == 1:
        cpu_value = cpu[0]
    else:
        cpu_value = None
    observed = bool(memory or cpu or disk)
    return {
        "resource_samples_observed": observed,
        "container_memory_bytes_peak": max(memory) if memory else None,
        "container_cpu_usage_usec": cpu_value,
        "container_filesystem_free_bytes": disk[-1] if disk else None,
        "docker_samples_observed": False,
        "docker_cpu_percent": None,
        "docker_memory_bytes": None,
        "docker_disk_bytes": None,
    }


def metrics_delta(
    before: tuple[bool, int | None],
    after: tuple[bool, int | None],
) -> tuple[bool, int | None]:
    """Combine two scrapes. A failed scrape stays unobserved and null."""

    if (
        len(before) != 2
        or len(after) != 2
        or type(before[0]) is not bool
        or type(after[0]) is not bool
    ):
        raise EnduranceSpecError("metrics observation is invalid")
    if before[0] is not True or after[0] is not True:
        return False, None
    if type(before[1]) is not int or type(after[1]) is not int:
        return False, None
    return True, after[1] - before[1]


def summarize_scenario(
    scenario: str,
    outcomes: Sequence[Mapping[str, object]],
    *,
    phase_seconds: float,
) -> dict[str, object]:
    selected = [item for item in outcomes if item.get("scenario") == scenario]
    latencies = [
        latency
        for item in selected
        if isinstance((latency := item.get("latency_ms")), int) and not isinstance(latency, bool)
    ]
    statuses: dict[str, int] = {}
    successes = expected = unexpected = 0
    for item in selected:
        label = str(item.get("classification"))
        if label == "success":
            successes += 1
        elif label == "expected_rejection":
            expected += 1
        else:
            unexpected += 1
        statuses[str(item.get("status"))] = statuses.get(str(item.get("status")), 0) + 1
    return {
        "scenario": scenario,
        "transport": "http",
        "count": len(selected),
        "successes": successes,
        "expected_rejections": expected,
        "unexpected_errors": unexpected,
        "status": statuses,
        "throughput_per_second": throughput(len(selected), phase_seconds),
        "latency_ms": {
            "p50": percentile_ms(latencies, 50) if latencies else None,
            "p95": percentile_ms(latencies, 95) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }


def judge(report: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if report.get("local_endurance_status") != "pass":
        reasons.append("endurance")
    if report.get("local_isolation_status") != "pass":
        reasons.append("isolation")
    if report.get("local_financial_reconciliation_status") != "pass":
        reasons.append("financial_reconciliation")
    if report.get("local_soak_status") != "not-run":
        reasons.append("soak")
    if report.get("duration_seconds") != DURATION_SECONDS or report.get("workers") != WORKERS:
        reasons.append("profile")
    if report.get("pace_seconds") != PACE_SECONDS:
        reasons.append("pacing")
    if report.get("planned_operations") != PLAN_OPERATIONS:
        reasons.append("operations")
    completed = report.get("completed_operations") != PLAN_OPERATIONS
    cancelled = report.get("cancelled_operations") != 0
    if completed or cancelled:
        reasons.append("cancelled")
    measured = report.get("measured_phase_seconds")
    wall = report.get("wall_time_seconds")
    if isinstance(measured, bool) or not isinstance(measured, (int, float)):
        reasons.append("duration")
    elif isinstance(wall, bool) or not isinstance(wall, (int, float)):
        reasons.append("duration")
    elif (
        float(measured) < MIN_MEASURED_PHASE_SECONDS
        or float(measured) > DURATION_SECONDS
        or float(wall) > DURATION_SECONDS + TIMEOUT_SECONDS
    ):
        reasons.append("duration")
    if report.get("peak_active_workers") != WORKERS:
        reasons.append("workers")
    if report.get("cross_gym_leakage") != 0:
        reasons.append("cross_gym_leakage")
    if report.get("credential_exposure") != 0:
        reasons.append("credential_exposure")
    if report.get("unexpected_errors") != 0 or report.get("api_unexpected_5xx") != 0:
        reasons.append("unexpected_errors")
    observed = report.get("caddy_metrics_observed")
    delta = report.get("caddy_5xx_delta")
    if observed is True:
        if type(delta) is not int or delta != 0:
            reasons.append("caddy_5xx")
    elif observed is False:
        if delta is not None:
            reasons.append("caddy_false_zero")
    else:
        reasons.append("caddy_metrics")
    if report.get("reconciliation_matched") is not True or report.get("data_corruption") is True:
        reasons.append("reconciliation")
    if not connection_within_ceiling(
        report.get("postgres_connection_peak"),
        report.get("postgres_max_connections"),
    ):
        reasons.append("connection_ceiling")
    if report.get("required_complete") is not True:
        reasons.append("required_steps")
    latency = report.get("http_p95_ms")
    if type(latency) is not int or latency > PROVISIONAL_P95_MS:
        reasons.append("provisional_latency_ceiling")
    if report.get("resource_samples_observed") is False:
        for key in (
            "container_memory_bytes_peak",
            "container_cpu_usage_usec",
            "container_filesystem_free_bytes",
        ):
            if report.get(key) is not None:
                reasons.append("resource_false_zero")
                break
    elif report.get("resource_samples_observed") is not True:
        reasons.append("resources")
    if report.get("docker_samples_observed") is False:
        for key in ("docker_cpu_percent", "docker_memory_bytes", "docker_disk_bytes"):
            if report.get(key) is not None:
                reasons.append("docker_false_zero")
                break
    elif report.get("docker_samples_observed") is not True:
        reasons.append("docker_samples")
    for flag in PROOF_FLAGS:
        if report.get(flag) is True:
            reasons.append("false_proof")
            break
    if report.get("thresholds_are_approved_slos") is not False:
        reasons.append("false_proof")
    return ("pass" if not reasons else "fail"), reasons


def _base_fields() -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": KIND,
        "profile": PROFILE_NAME,
        "evidence_name": EVIDENCE_NAME,
        "local_endurance_status": "fail",
        "local_isolation_status": "not-run",
        "local_financial_reconciliation_status": "not-run",
        "local_soak_status": "not-run",
        "thresholds_are_approved_slos": False,
        "duration_seconds": DURATION_SECONDS,
        "pace_seconds": PACE_SECONDS,
        "workers": WORKERS,
        "planned_operations": PLAN_OPERATIONS,
        "completed_operations": 0,
        "cancelled_operations": 0,
        "measured_phase_seconds": 0.0,
        "wall_time_seconds": 0.0,
        "throughput_per_second": None,
        "peak_active_workers": 0,
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
        "required_complete": False,
        "http_p95_ms": None,
        "resource_samples_observed": False,
        "container_memory_bytes_peak": None,
        "container_cpu_usage_usec": None,
        "container_filesystem_free_bytes": None,
        "docker_samples_observed": False,
        "docker_cpu_percent": None,
        "docker_memory_bytes": None,
        "docker_disk_bytes": None,
        "provisional_local_p95_ceiling_ms": PROVISIONAL_P95_MS,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "failure_stage": "",
        "failure_detail": "",
    }
    for flag in PROOF_FLAGS:
        body[flag] = False
    return body


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    if profile != PROFILE_NAME:
        raise EnduranceSpecError("endurance profile is not the fixed profile")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise EnduranceSpecError("failure stage is invalid")
    body = _base_fields()
    body["failure_stage"] = stage
    return body


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in PROOF_FLAGS:
        if body.get(flag) is True:
            raise EnduranceSpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("thresholds_are_approved_slos") is not False:
        raise EnduranceSpecError("provisional thresholds were described as approved SLOs")
    if body.get("local_soak_status") != "not-run":
        raise EnduranceSpecError("local soak cannot be claimed by this profile")
    if body.get("schema_version") not in (None, SCHEMA):
        raise EnduranceSpecError("endurance evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["provisional_thresholds"] = True
    body["approved_slo"] = False
    body["evidence_name"] = EVIDENCE_NAME
    for key in (
        "local_endurance_status",
        "local_isolation_status",
        "local_financial_reconciliation_status",
    ):
        if body.get(key) not in LOCAL_STATUSES:
            raise EnduranceSpecError("local status must be pass, fail, or not-run")
    if body.get("local_endurance_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise EnduranceSpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets):
        raise EnduranceSpecError("endurance evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise EnduranceSpecError("endurance evidence contains secret material")
    body["checksum"] = checksum_for(body)
    return body


def verify_checksum(body: Mapping[str, Any]) -> bool:
    expected = body.get("checksum")
    if not isinstance(expected, str):
        return False
    return expected == checksum_for(body)


def lint_texts(*, command: str, runbook: str, harness: str) -> list[str]:
    errors: list[str] = []
    for phrase in (
        "config.settings.staging_rehearsal",
        "STAGING_REHEARSAL",
        "gym-staging-rehearsal",
        "load_test_proven",
        "soak_test_proven",
        "production_capacity_proven",
        "slo_proven",
        "financial_reconciliation_proven",
    ):
        if phrase not in command:
            errors.append(f"endurance command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "endurance.json",
        "pt-cash.json",
        "pt.json",
        "load.json",
        "finance.json",
        "bounded endurance",
        "4 GiB",
        "not an approved SLO",
        "local_endurance_status",
        "local_soak_status",
        "not-run",
        "dashboard",
        "PT report",
        "check-in",
        "expense",
        "cash session",
        "service-only",
        "uncovered",
        "soak",
        "caddy_metrics_observed",
        "unavailable",
        "financial_reconciliation_proven",
    ):
        if phrase not in runbook:
            errors.append(f"endurance runbook is missing {phrase}")
    for phrase in (
        "soak test is proven",
        "load test is proven",
        "production capacity is proven",
        "SLO is proven",
        "financial reconciliation is proven",
    ):
        if phrase in runbook:
            errors.append(f"endurance runbook claims {phrase}")
    for phrase in (
        "endurance.json",
        "staging_endurance_characterization",
        "does not replace pt.json",
        "does not replace pt-cash.json",
        "does not replace load.json",
        "does not replace finance.json",
    ):
        if phrase not in harness:
            errors.append(f"staging harness is missing {phrase}")
    return errors


__all__ = [
    "COVERAGE",
    "CYCLE",
    "DURATION_SECONDS",
    "EVIDENCE_NAME",
    "KIND",
    "LOOP_OPERATIONS",
    "MAX_RESPONSE_BYTES",
    "MIN_MEASURED_PHASE_SECONDS",
    "PACE_SECONDS",
    "PLAN_OPERATIONS",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PROVISIONAL_P95_MS",
    "REGISTER_CODE",
    "REPEATS",
    "RULES",
    "SCHEMA",
    "SETUP",
    "TIMEOUT_SECONDS",
    "WORKERS",
    "EnduranceSpecError",
    "Profile",
    "assert_endurance_path",
    "assess_endurance",
    "assess_isolation",
    "build_plan",
    "caddy_metrics_observation",
    "checksum_for",
    "classify_status",
    "clip_body",
    "connection_within_ceiling",
    "expected_writes",
    "failure_document",
    "judge",
    "lint_texts",
    "loop_queue_cap",
    "make_key",
    "members_required",
    "metrics_delta",
    "operation_cap",
    "pace_delay",
    "resolve_profile",
    "same_gym_link_mismatches",
    "schedule_match",
    "seal_evidence",
    "summarize_resources",
    "summarize_scenario",
    "throughput",
    "validate_profile",
    "verify_checksum",
]

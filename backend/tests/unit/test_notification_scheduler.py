from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import get_commands, load_command_class
from django.db import OperationalError

from apps.common.production import (
    PRODUCTION_SCHEDULER_COMMANDS,
    PRODUCTION_SCHEDULER_INTERVALS_SECONDS,
    require_scheduler_intervals,
)
from apps.notifications import scheduler as scheduler_module
from apps.notifications.scheduler import (
    COMMAND_WATCHDOG_SECONDS,
    HEARTBEAT_MAX_AGE_SECONDS,
    HEARTBEAT_PATH,
    NotificationScheduler,
    execute_registered_command,
    render_scheduler_metrics,
    validate_scheduler_configuration,
    write_scheduler_metrics,
)
from apps.notifications.scheduler_locks import assert_distinct_lock_ids, lock_keys

ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class MemoryLock:
    def __init__(self) -> None:
        self.held: set[str] = set()

    def try_acquire(self, command: str) -> bool:
        if command in self.held:
            return False
        self.held.add(command)
        return True

    def release(self, command: str) -> None:
        self.held.discard(command)


class StopAfterWait(threading.Event):
    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        self.set()
        return True


def scheduler(tmp_path: Path, execute, lock: MemoryLock | None = None, clock: Clock | None = None):
    return NotificationScheduler(
        execute=execute,
        lock=lock if lock is not None else MemoryLock(),
        clock=clock if clock is not None else Clock(),
        heartbeat_path=tmp_path / "heartbeat",
        stop=threading.Event(),
    )


def test_registry_schedules_the_six_commands_at_the_documented_intervals() -> None:
    validate_scheduler_configuration()
    assert_distinct_lock_ids()
    assert PRODUCTION_SCHEDULER_INTERVALS_SECONDS == {
        "schedule_membership_expiry_emails": 900,
        "deliver_membership_expiry_emails": 300,
        "schedule_membership_expiry_pushes": 900,
        "deliver_push_notifications": 120,
        "publish_scheduled_announcements": 60,
        "expire_announcements": 300,
        "refresh_membership_statuses": 900,
        "expire_profile_photo_uploads": 3600,
    }
    assert tuple(PRODUCTION_SCHEDULER_INTERVALS_SECONDS) == PRODUCTION_SCHEDULER_COMMANDS
    ids = [lock_keys(name)[1] for name in PRODUCTION_SCHEDULER_COMMANDS]
    assert len(set(ids)) == 8
    commands = get_commands()
    assert commands["run_notification_scheduler"] == "apps.notifications"
    for name in PRODUCTION_SCHEDULER_COMMANDS:
        load_command_class(commands[name], name)


def test_invalid_intervals_and_unknown_commands_are_rejected() -> None:
    changed = dict(PRODUCTION_SCHEDULER_INTERVALS_SECONDS)
    changed["publish_scheduled_announcements"] = 120
    with pytest.raises(ImproperlyConfigured):
        require_scheduler_intervals(changed)
    missing = {
        name: seconds
        for name, seconds in PRODUCTION_SCHEDULER_INTERVALS_SECONDS.items()
        if name != "expire_announcements"
    }
    with pytest.raises(ImproperlyConfigured):
        require_scheduler_intervals(missing)
    with pytest.raises(ImproperlyConfigured):
        execute_registered_command("echo")
    with pytest.raises(ImproperlyConfigured):
        lock_keys("echo")


def test_one_failure_does_not_stop_later_commands_or_keep_the_lock(tmp_path: Path) -> None:
    calls: list[str] = []
    lock = MemoryLock()

    def execute(name: str) -> None:
        calls.append(name)
        if name == "schedule_membership_expiry_emails":
            raise RuntimeError("member@example.com RESEND_API_KEY=re_secret")

    results = scheduler(tmp_path, execute, lock).run_due()
    assert calls == list(PRODUCTION_SCHEDULER_COMMANDS)
    assert results[0].status == "failed"
    assert results[0].error_type == "RuntimeError"
    assert [item.status for item in results[1:]] == ["finished"] * (
        len(PRODUCTION_SCHEDULER_COMMANDS) - 1
    )
    assert lock.held == set()


def test_failure_logs_do_not_include_the_exception_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def execute(name: str) -> None:
        if name == "deliver_push_notifications":
            raise RuntimeError("member@example.com token=plain-device-token")

    with caplog.at_level("INFO", logger="gym.scheduler"):
        scheduler(tmp_path, execute).run_due()
    text = caplog.text
    assert "command_failed" in text
    assert "error_type=RuntimeError" in text
    assert "member@example.com" not in text
    assert "plain-device-token" not in text
    assert "RESEND_API_KEY" not in text


def test_a_held_lock_skips_only_that_command(tmp_path: Path) -> None:
    lock = MemoryLock()
    assert lock.try_acquire("deliver_push_notifications")
    calls: list[str] = []
    results = scheduler(tmp_path, calls.append, lock).run_due()
    by_name = {item.command: item.status for item in results}
    assert by_name["deliver_push_notifications"] == "skipped"
    assert "deliver_push_notifications" not in calls
    assert by_name["publish_scheduled_announcements"] == "finished"
    assert "deliver_push_notifications" in lock.held


def test_shutdown_does_not_start_the_next_command(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = scheduler(tmp_path, calls.append)

    def execute(name: str) -> None:
        calls.append(name)
        runner.stop.set()

    runner.execute = execute
    runner.run_due()
    assert calls == ["schedule_membership_expiry_emails"]


def test_restart_runs_due_commands_again_without_a_private_queue(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[str] = []
    first = scheduler(tmp_path, calls.append, clock=clock)
    first.run_due()
    assert calls.count("schedule_membership_expiry_emails") == 1
    first.run_due()
    assert calls.count("schedule_membership_expiry_emails") == 1
    clock.now = 60
    first.run_due()
    assert calls.count("publish_scheduled_announcements") == 2
    restarted = scheduler(tmp_path, calls.append, clock=clock)
    restarted.run_due()
    assert calls.count("schedule_membership_expiry_emails") == 2


def test_serve_stops_after_one_pass(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = NotificationScheduler(
        execute=calls.append,
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "heartbeat",
        stop=StopAfterWait(),
    )
    runner.serve()
    assert calls == list(PRODUCTION_SCHEDULER_COMMANDS)
    text = (tmp_path / "heartbeat").read_text(encoding="ascii")
    assert text.startswith("ok ")


def test_scheduler_metrics_file_reports_ok_and_degraded(tmp_path: Path) -> None:
    assert "gym_scheduler_heartbeat_ok 1" in render_scheduler_metrics("ok", 100)
    assert "gym_scheduler_heartbeat_ok 0" in render_scheduler_metrics("degraded", 100)
    path = tmp_path / "scheduler.prom"
    write_scheduler_metrics(path, "ok", now=100)
    text = path.read_text(encoding="ascii")
    assert "gym_scheduler_heartbeat_timestamp_seconds 100" in text
    assert "gym_scheduler_heartbeat_ok 1" in text
    runner = NotificationScheduler(
        execute=lambda name: None,
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "heartbeat",
        metrics_path=path,
        stop=StopAfterWait(),
    )
    runner.serve()
    refreshed = path.read_text(encoding="ascii")
    assert "gym_scheduler_heartbeat_ok 1" in refreshed
    assert "gym_scheduler_heartbeat_timestamp_seconds 100\n" not in refreshed


def test_heartbeat_rejects_stale_degraded_fatal_and_missing_states(tmp_path: Path) -> None:
    module = _healthcheck()
    assert COMMAND_WATCHDOG_SECONDS == 3600
    assert module.HEARTBEAT_PATH == HEARTBEAT_PATH
    assert module.HEARTBEAT_MAX_AGE_SECONDS == HEARTBEAT_MAX_AGE_SECONDS
    assert module.evaluate("ok 1000", now=1000) == 0
    assert module.evaluate("ok 1000", now=1000 + HEARTBEAT_MAX_AGE_SECONDS + 1) == 1
    assert module.evaluate("degraded 1000", now=1000) == 1
    assert module.evaluate("fatal 1000", now=1000) == 1
    assert module.evaluate("ok later", now=1000) == 1
    assert module.evaluate("", now=1000) == 1
    assert module.main(tmp_path / "missing") == 1


def _healthcheck():
    path = ROOT / "backend" / "docker" / "scheduler_healthcheck.py"
    spec = importlib.util.spec_from_file_location("scheduler_healthcheck", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("scheduler healthcheck is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(path: Path) -> int:
    return int(path.read_text(encoding="ascii").split()[1])


def test_heartbeat_stays_fresh_during_a_command_and_stops_when_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[str] = []
    real = scheduler_module.write_heartbeat

    def spy(path: Path, status: str, now: int | None = None) -> None:
        writes.append(status)
        real(path, status, now=now)

    monkeypatch.setattr(scheduler_module, "write_heartbeat", spy)
    fresh = NotificationScheduler(
        execute=lambda _name: time.sleep(0.12),
        lock=MemoryLock(),
        heartbeat_path=tmp_path / "fresh",
        heartbeat_interval_seconds=0.03,
        command_watchdog_seconds=30,
        stop=threading.Event(),
    )
    fresh._run_one("deliver_push_notifications")
    assert writes
    assert set(writes) == {"ok"}
    assert len(writes) >= 3
    health = _healthcheck()
    text = (tmp_path / "fresh").read_text(encoding="ascii")
    assert health.evaluate(text, now=_stamp(tmp_path / "fresh")) == 0

    stuck_writes: list[str] = []

    def stuck_spy(path: Path, status: str, now: int | None = None) -> None:
        stuck_writes.append(status)
        real(path, status, now=now)

    monkeypatch.setattr(scheduler_module, "write_heartbeat", stuck_spy)
    stuck = NotificationScheduler(
        execute=lambda _name: time.sleep(0.12),
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "stuck",
        heartbeat_interval_seconds=0.03,
        command_watchdog_seconds=0,
        stop=threading.Event(),
    )
    stuck._run_one("deliver_push_notifications")
    assert stuck_writes == ["ok"]
    assert (
        health.evaluate(
            (tmp_path / "stuck").read_text(encoding="ascii"),
            now=_stamp(tmp_path / "stuck") + HEARTBEAT_MAX_AGE_SECONDS + 1,
        )
        == 1
    )


def test_heartbeat_stops_after_shutdown_and_reports_database_state(tmp_path: Path) -> None:
    runner = NotificationScheduler(
        execute=lambda _name: None,
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "heartbeat",
        stop=StopAfterWait(),
    )
    runner.serve()
    text = (tmp_path / "heartbeat").read_text(encoding="ascii")
    time.sleep(0.05)
    assert (tmp_path / "heartbeat").read_text(encoding="ascii") == text
    assert not any(item.name == "gym-scheduler-command" for item in threading.enumerate())

    def fail_database(name: str) -> None:
        if name == "schedule_membership_expiry_emails":
            raise OperationalError("postgres is down")

    degraded = NotificationScheduler(
        execute=fail_database,
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "degraded",
        stop=StopAfterWait(),
    )
    degraded.serve()
    assert (tmp_path / "degraded").read_text(encoding="ascii").startswith("ok ")

    def fail_all(_name: str) -> None:
        raise OperationalError("postgres is down")

    failed = NotificationScheduler(
        execute=fail_all,
        lock=MemoryLock(),
        clock=Clock(),
        heartbeat_path=tmp_path / "failed",
        stop=StopAfterWait(),
    )
    failed.serve()
    assert (tmp_path / "failed").read_text(encoding="ascii").startswith("degraded ")

    during = NotificationScheduler(
        execute=lambda _name: time.sleep(0.08),
        lock=MemoryLock(),
        heartbeat_path=tmp_path / "during",
        heartbeat_interval_seconds=0.02,
        command_watchdog_seconds=30,
        stop=threading.Event(),
    )
    during.database_up = False
    during._run_one("deliver_push_notifications")
    assert (tmp_path / "during").read_text(encoding="ascii").startswith("degraded ")


def test_stop_request_finishes_the_current_command_only(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = scheduler(tmp_path, calls.append)

    def execute(name: str) -> None:
        calls.append(name)
        runner.stop.set()

    runner.execute = execute
    runner.serve()
    assert calls == ["schedule_membership_expiry_emails"]


def test_a_loop_error_stays_in_process_and_marks_the_heartbeat_degraded(tmp_path: Path) -> None:
    class Boom(MemoryLock):
        def try_acquire(self, command: str) -> bool:
            raise RuntimeError("boom")

    runner = NotificationScheduler(
        execute=lambda _name: None,
        lock=Boom(),
        clock=Clock(),
        heartbeat_path=tmp_path / "heartbeat",
        stop=StopAfterWait(),
    )
    runner.serve()
    assert (tmp_path / "heartbeat").read_text(encoding="ascii").startswith("degraded ")


def test_the_command_thread_closes_only_its_own_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []
    original = NotificationScheduler._close

    def spy(self: NotificationScheduler) -> None:
        closed.append(threading.current_thread().name)
        original(self)

    monkeypatch.setattr(NotificationScheduler, "_close", spy)
    NotificationScheduler(
        execute=lambda _name: None,
        lock=MemoryLock(),
        heartbeat_path=tmp_path / "heartbeat",
        stop=threading.Event(),
    )._run_one("deliver_push_notifications")
    assert "gym-scheduler-command" in closed
    assert threading.main_thread().name in closed


def test_the_advisory_lock_is_held_for_the_whole_command(tmp_path: Path) -> None:
    lock = MemoryLock()
    seen: list[bool] = []

    def execute(name: str) -> None:
        seen.append(name in lock.held)

    scheduler(tmp_path, execute, lock).run_due()
    assert seen == [True] * len(PRODUCTION_SCHEDULER_COMMANDS)
    assert lock.held == set()


def test_production_compose_scheduler_has_no_public_port() -> None:
    artifacts = ROOT / "scripts" / "production_artifacts.py"
    spec = importlib.util.spec_from_file_location("production_artifacts", artifacts)
    if spec is None or spec.loader is None:
        raise RuntimeError("production artifact module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["scheduler"]
    environment = service["environment"]
    assert "ports" not in service
    assert service["restart"] == "unless-stopped"
    assert service["healthcheck"]["interval"] == "30s"
    assert service["healthcheck"]["timeout"] == "15s"
    assert service["healthcheck"]["retries"] == "3"
    assert service["healthcheck"]["start_period"] == "120s"
    assert service["cpus"] == "0.05"
    assert service["mem_limit"] == "160m"
    assert service["pids_limit"] == "64"
    assert environment["DJANGO_SETTINGS_MODULE"] == "config.settings.production"
    assert environment["EMAIL_BACKEND"] == "apps.common.mail.resend.EmailBackend"
    assert environment["PUSH_BACKEND"] == "fcm"
    assert "ASSISTANT_E2E_SCRIPT" not in environment
    assert "locmem" not in " ".join(environment.values())
    rendered = " ".join(str(value) for value in environment.values())
    assert "re_" not in rendered
    assert "BEGIN" not in rendered

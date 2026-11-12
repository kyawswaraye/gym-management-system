"""Single-process production runner for the notification management commands.

One Compose service runs this loop. It does not fork a scheduler inside each
API worker. PostgreSQL session advisory locks use the two-integer key space,
which is separate from the one-integer class-location locks. A second
scheduler session cannot run the same command. The lock ends when the session
disconnects, including when the process is killed.

The main thread rewrites the heartbeat while a command runs, and it stops
rewriting when that command exceeds the watchdog. A dead process stops
writing because the thread dies with it. A command that never returns stops
receiving fresh timestamps after the watchdog, so the existing stale check
still fails. The advisory lock stays on this thread for the whole command.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import InterfaceError, OperationalError, connection
from django.db.utils import Error as DatabaseError

from apps.common.production import (
    PRODUCTION_SCHEDULER_COMMANDS,
    PRODUCTION_SCHEDULER_INTERVALS_SECONDS,
    require_scheduler_commands,
    require_scheduler_intervals,
)

logger = logging.getLogger("gym.scheduler")

HEARTBEAT_PATH = Path("/tmp/gym-scheduler-heartbeat")
HEARTBEAT_MAX_AGE_SECONDS = 900
HEARTBEAT_INTERVAL_SECONDS = 5
# One gym of 100 provider calls at the 10s timeout is about 1000s. The
# refresher covers that batch, then stops so a stuck command cannot stay
# healthy past this bound plus the stale window.
COMMAND_WATCHDOG_SECONDS = 3600
_DATABASE_ERRORS = frozenset({"OperationalError", "InterfaceError"})


def validate_scheduler_configuration() -> None:
    """Reject a drifted command list, interval, or lock id before the loop."""

    require_scheduler_commands(PRODUCTION_SCHEDULER_COMMANDS)
    require_scheduler_intervals(PRODUCTION_SCHEDULER_INTERVALS_SECONDS)
    from apps.notifications.scheduler_locks import assert_distinct_lock_ids

    assert_distinct_lock_ids()


def execute_registered_command(name: str) -> None:
    """Run one registry command through Django. Names are not shell input."""

    if name not in PRODUCTION_SCHEDULER_COMMANDS:
        raise ImproperlyConfigured("Unknown scheduler command")
    call_command(name)


def write_heartbeat(path: Path, status: str, *, now: int | None = None) -> None:
    if status not in {"ok", "degraded", "fatal"}:
        status = "degraded"
    stamp = int(time.time() if now is None else now)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(f"{status} {stamp}\n", encoding="ascii")
    temporary.replace(path)


def render_scheduler_metrics(status: str, stamp: int) -> str:
    """Prometheus textfile body. It contains a status and a timestamp only."""

    if status not in {"ok", "degraded", "fatal"}:
        status = "degraded"
    ok = 1 if status == "ok" else 0
    return (
        "# HELP gym_scheduler_heartbeat_timestamp_seconds "
        "Unix time of the last scheduler heartbeat.\n"
        "# TYPE gym_scheduler_heartbeat_timestamp_seconds gauge\n"
        f"gym_scheduler_heartbeat_timestamp_seconds {stamp}\n"
        "# HELP gym_scheduler_heartbeat_ok 1 when the scheduler heartbeat is ok.\n"
        "# TYPE gym_scheduler_heartbeat_ok gauge\n"
        f"gym_scheduler_heartbeat_ok {ok}\n"
    )


def write_scheduler_metrics(path: Path, status: str, *, now: int | None = None) -> None:
    stamp = int(time.time() if now is None else now)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(render_scheduler_metrics(status, stamp), encoding="ascii")
    temporary.replace(path)


@dataclass(frozen=True)
class CommandRun:
    command: str
    status: str
    duration_ms: int
    error_type: str = ""

    @property
    def database_unavailable(self) -> bool:
        return self.status == "failed" and self.error_type in _DATABASE_ERRORS


class CommandLock(Protocol):
    def try_acquire(self, command: str) -> bool: ...

    def release(self, command: str) -> None: ...


class NotificationScheduler:
    """Run due commands one at a time until asked to stop."""

    def __init__(
        self,
        *,
        stop: threading.Event | None = None,
        execute: Callable[[str], None] = execute_registered_command,
        lock: CommandLock | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_path: Path = HEARTBEAT_PATH,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        command_watchdog_seconds: float = COMMAND_WATCHDOG_SECONDS,
        metrics_path: Path | None = None,
    ) -> None:
        self.stop = stop if stop is not None else threading.Event()
        self.execute = execute
        self.lock: CommandLock = lock if lock is not None else AdvisoryLock()
        self.clock = clock
        self.heartbeat_path = heartbeat_path
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.command_watchdog_seconds = command_watchdog_seconds
        self.metrics_path = metrics_path
        self.next_due = dict.fromkeys(PRODUCTION_SCHEDULER_COMMANDS, 0.0)
        self.database_up = True

    def serve(self) -> None:
        _log("scheduler_started")
        self._beat("ok")
        while not self.stop.is_set():
            try:
                results = self.run_due()
            except Exception as exc:
                self.database_up = False
                self._beat("degraded")
                _log("scheduler_loop_failed", error_type=type(exc).__name__)
            else:
                if results:
                    self.database_up = any(not item.database_unavailable for item in results)
                self._beat("ok" if self.database_up else "degraded")
            if self.stop.is_set():
                break
            self.stop.wait(self._pause())
        _log("scheduler_stopped")

    def run_due(self) -> list[CommandRun]:
        moment = self.clock()
        results: list[CommandRun] = []
        for name in PRODUCTION_SCHEDULER_COMMANDS:
            if self.stop.is_set():
                break
            if moment < self.next_due[name]:
                continue
            results.append(self._run_one(name))
            self.next_due[name] = self.clock() + PRODUCTION_SCHEDULER_INTERVALS_SECONDS[name]
        return results

    def _run_one(self, name: str) -> CommandRun:
        if name not in PRODUCTION_SCHEDULER_COMMANDS:
            raise ImproperlyConfigured("Unknown scheduler command")
        started = self.clock()
        self._beat("ok" if self.database_up else "degraded")
        _log("command_started", command=name)
        try:
            acquired = self.lock.try_acquire(name)
        except (OperationalError, InterfaceError, DatabaseError) as exc:
            self._close()
            return self._finish(name, started, "failed", type(exc).__name__)
        if not acquired:
            _log("command_skipped", command=name, reason="lock_held")
            return CommandRun(name, "skipped", self._elapsed(started), "")
        error = self._execute_while_held(name, started)
        try:
            if error is None:
                return self._finish(name, started, "finished", "")
            return self._finish(name, started, "failed", type(error).__name__)
        finally:
            try:
                self.lock.release(name)
            except (OperationalError, InterfaceError, DatabaseError):
                self._close()
            else:
                self._close()

    def _execute_while_held(self, name: str, started: float) -> BaseException | None:
        """Run the command off this thread so the lock session stays here.

        This thread rewrites the heartbeat until the watchdog. It does not
        unlock, and it does not keep writing after the watchdog. A dead
        process cannot continue the writes.
        """

        outcome: list[BaseException] = []
        finished = threading.Event()

        def run() -> None:
            try:
                self.execute(name)
            except Exception as exc:
                outcome.append(exc)
            finally:
                # The command uses this thread's connection. Closing it does
                # not release the advisory lock, which is on the main thread.
                self._close()
                finished.set()

        worker = threading.Thread(target=run, name="gym-scheduler-command")
        worker.start()
        watchdog_logged = False
        try:
            while not finished.wait(self.heartbeat_interval_seconds):
                if self.clock() - started >= self.command_watchdog_seconds:
                    if not watchdog_logged:
                        _log(
                            "scheduler_command_watchdog",
                            command=name,
                            duration_ms=self._elapsed(started),
                        )
                        watchdog_logged = True
                    continue
                self._beat("ok" if self.database_up else "degraded")
        finally:
            worker.join()
        return outcome[0] if outcome else None

    def _finish(self, name: str, started: float, status: str, error_type: str) -> CommandRun:
        duration_ms = self._elapsed(started)
        if status == "failed":
            _log(
                "command_failed",
                command=name,
                duration_ms=duration_ms,
                exit_status=1,
                error_type=error_type,
            )
        else:
            _log(
                "command_finished",
                command=name,
                duration_ms=duration_ms,
                exit_status=0,
            )
        return CommandRun(name, status, duration_ms, error_type)

    def _elapsed(self, started: float) -> int:
        return max(0, int((self.clock() - started) * 1000))

    def _pause(self) -> float:
        moment = self.clock()
        remaining = [due - moment for due in self.next_due.values()]
        delay = min(remaining) if remaining else self.heartbeat_interval_seconds
        if delay < 0:
            delay = 0
        return min(delay, self.heartbeat_interval_seconds)

    def _beat(self, status: str) -> None:
        try:
            write_heartbeat(self.heartbeat_path, status)
        except OSError as exc:
            _log("scheduler_heartbeat_failed", error_type=type(exc).__name__)
        if self.metrics_path is None:
            return
        try:
            write_scheduler_metrics(self.metrics_path, status)
        except OSError as exc:
            _log("scheduler_metrics_failed", error_type=type(exc).__name__)

    def _close(self) -> None:
        try:
            connection.close()
        except DatabaseError:
            return


class AdvisoryLock:
    """Session lock. Unlock, then close the connection, after every attempt."""

    def try_acquire(self, command: str) -> bool:
        from apps.notifications.scheduler_locks import lock_keys

        namespace, command_id = lock_keys(command)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                [namespace, command_id],
            )
            row = cursor.fetchone()
        return bool(row and row[0])

    def release(self, command: str) -> None:
        from apps.notifications.scheduler_locks import lock_keys

        namespace, command_id = lock_keys(command)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [namespace, command_id])


def _log(event: str, **fields: object) -> None:
    parts = [event]
    for key in ("command", "duration_ms", "exit_status", "error_type", "reason"):
        if key in fields:
            value = fields[key]
            if not isinstance(value, str | int):
                continue
            parts.append(f"{key}={value}")
    logger.info(" ".join(parts))

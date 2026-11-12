from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from django.db import connection

from apps.common.production import PRODUCTION_SCHEDULER_COMMANDS
from apps.notifications.scheduler import AdvisoryLock, NotificationScheduler
from apps.notifications.scheduler_locks import lock_keys

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _connect() -> psycopg.Connection:
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        autocommit=True,
    )


def _try_lock(session: psycopg.Connection, command: str) -> bool:
    namespace, command_id = lock_keys(command)
    with session.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [namespace, command_id])
        row = cursor.fetchone()
    return bool(row and row[0])


def _unlock(session: psycopg.Connection, command: str) -> None:
    namespace, command_id = lock_keys(command)
    with session.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [namespace, command_id])


def test_command_locks_are_independent_and_do_not_block_queries() -> None:
    first = PRODUCTION_SCHEDULER_COMMANDS[0]
    second = PRODUCTION_SCHEDULER_COMMANDS[1]
    other = _connect()
    held = _connect()
    try:
        lock = AdvisoryLock()
        assert lock.try_acquire(first)
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
            assert _try_lock(other, first) is False
            assert _try_lock(other, second) is True
        finally:
            lock.release(first)
        assert _try_lock(other, first) is True
        _unlock(other, first)
        _unlock(other, second)
        assert _try_lock(held, second) is True
        held.close()
        assert _try_lock(other, second) is True
        _unlock(other, second)
    finally:
        other.close()
        if not held.closed:
            held.close()


def test_lock_stays_held_until_the_command_returns(tmp_path: Path) -> None:
    other = _connect()
    seen: list[bool] = []

    def execute(name: str) -> None:
        acquired = _try_lock(other, name)
        seen.append(acquired)
        if acquired:
            _unlock(other, name)

    try:
        NotificationScheduler(
            execute=execute,
            lock=AdvisoryLock(),
            heartbeat_path=tmp_path / "heartbeat",
        ).run_due()
    finally:
        other.close()
    assert seen == [False] * len(PRODUCTION_SCHEDULER_COMMANDS)


def test_lock_releases_when_the_command_raises() -> None:
    command = PRODUCTION_SCHEDULER_COMMANDS[2]
    other = _connect()
    lock = AdvisoryLock()
    try:
        assert lock.try_acquire(command)
        with pytest.raises(RuntimeError):
            try:
                raise RuntimeError("provider failed")
            finally:
                lock.release(command)
        assert _try_lock(other, command) is True
        _unlock(other, command)
    finally:
        other.close()

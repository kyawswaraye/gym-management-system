"""Advisory-lock keys for the notification scheduler.

The namespace is a fixed integer. The command id is a SHA-256 prefix, not
Python's randomized hash. Distinct commands are checked at startup so two
commands cannot share a key.
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import ImproperlyConfigured

from apps.common.production import PRODUCTION_SCHEDULER_COMMANDS

# Fixed class id for pg_try_advisory_lock(int, int). It is not derived from a
# secret and it is not a row id. The two-integer lock space is separate from
# the one-integer class-location locks.
LOCK_NAMESPACE = 1_647_384_801


def lock_keys(command: str) -> tuple[int, int]:
    if command not in PRODUCTION_SCHEDULER_COMMANDS:
        raise ImproperlyConfigured("Unknown scheduler command")
    digest = hashlib.sha256(f"gympilot.notification-scheduler.v1:{command}".encode()).digest()
    command_id = int.from_bytes(digest[:4], "big", signed=True)
    return LOCK_NAMESPACE, command_id


def assert_distinct_lock_ids() -> None:
    seen: dict[int, str] = {}
    for command in PRODUCTION_SCHEDULER_COMMANDS:
        namespace, command_id = lock_keys(command)
        if namespace != LOCK_NAMESPACE or command_id in seen:
            raise ImproperlyConfigured("Scheduler lock ids are not distinct")
        seen[command_id] = command

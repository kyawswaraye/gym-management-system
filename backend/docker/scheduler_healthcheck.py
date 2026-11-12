"""Fail unless the scheduler loop has written a fresh ok heartbeat.

A running container is not enough. The loop rewrites this file while it is
idle, before each command, and during a command that is still inside the
watchdog. A database outage is marked degraded. A dead process stops writing.
A command that stays inside the watchdog past the limit also stops receiving
new timestamps, so this same age check reports it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HEARTBEAT_PATH = Path("/tmp/gym-scheduler-heartbeat")
HEARTBEAT_MAX_AGE_SECONDS = 900


def evaluate(text: str, *, now: int) -> int:
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != "ok":
        return 1
    try:
        stamp = int(parts[1])
    except ValueError:
        return 1
    if stamp < 0 or stamp > now + 5 or now - stamp > HEARTBEAT_MAX_AGE_SECONDS:
        return 1
    return 0


def main(path: Path = HEARTBEAT_PATH) -> int:
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return 1
    return evaluate(text, now=int(time.time()))


if __name__ == "__main__":
    sys.exit(main())

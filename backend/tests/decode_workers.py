"""Spawn-safe decode workers. Each target is importable by a fresh interpreter."""

from __future__ import annotations

import os
import time
from multiprocessing.connection import Connection

from apps.files.constants import MAX_UPLOAD_BYTES


def sleep_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    del data, declared_type
    try:
        time.sleep(30)
    finally:
        connection.close()


def crash_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    del data, declared_type, connection
    os._exit(1)


def oversized_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    del data, declared_type
    payload = b"O" + bytes((9,)) + b"image/png" + (b"x" * (MAX_UPLOAD_BYTES + 1))
    try:
        connection.send_bytes(payload)
    except Exception:
        return
    finally:
        connection.close()


def invalid_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    del data, declared_type
    try:
        connection.send_bytes(b"not-a-frame")
    except Exception:
        return
    finally:
        connection.close()


def unknown_code_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    del data, declared_type
    label = b"PROVIDER_TRACE_1234"
    try:
        connection.send_bytes(b"R" + bytes((len(label),)) + label)
    except Exception:
        return
    finally:
        connection.close()

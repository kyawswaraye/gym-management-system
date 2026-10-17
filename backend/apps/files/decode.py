"""Decode one image in a process the parent can terminate."""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess

from apps.files.constants import ALLOWED_CONTENT_TYPES, DECODE_TIMEOUT_SECONDS, MAX_UPLOAD_BYTES
from apps.files.exceptions import ImageRejected
from apps.files.images import CanonicalImage, canonicalize

_JOIN_SECONDS = 2.0
_MAX_LABEL = 127
_MAX_FRAME = 2 + _MAX_LABEL + MAX_UPLOAD_BYTES
_STABLE_CODES = frozenset(
    {
        "FILE_EMPTY",
        "FILE_IMAGE_TOO_LARGE",
        "FILE_INVALID_DIMENSIONS",
        "FILE_MALFORMED",
        "FILE_TOO_LARGE",
        "FILE_TYPE_MISMATCH",
    }
)

DecodeWorker = Callable[[bytes, str, Connection], None]


def bounded_canonicalize(data: bytes, declared_type: str) -> CanonicalImage:
    """Production decoder. The child is terminated if it exceeds the time limit."""

    return run_bounded_decode(
        data,
        declared_type,
        timeout=float(DECODE_TIMEOUT_SECONDS),
        worker=canonicalize_worker,
    )


def run_bounded_decode(
    data: bytes,
    declared_type: str,
    *,
    timeout: float,
    worker: DecodeWorker,
) -> CanonicalImage:
    """Run a spawn-compatible worker and reap it on success, crash, or timeout.

    The parent sends only the upload bytes and declared type. The child replies
    with one raw frame no larger than the 5 MiB contract. Exceptions are not
    pickled across the pipe.
    """

    if not isinstance(data, bytes) or not isinstance(declared_type, str):
        raise ImageRejected("FILE_MALFORMED")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected("FILE_TOO_LARGE")
    if len(data) < 1:
        raise ImageRejected("FILE_EMPTY")
    if declared_type not in ALLOWED_CONTENT_TYPES or len(declared_type) > _MAX_LABEL:
        raise ImageRejected("FILE_TYPE_MISMATCH")
    if timeout <= 0:
        raise ImageRejected("FILE_IMAGE_TOO_LARGE")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process: BaseProcess = context.Process(
        target=worker,
        args=(data, declared_type, child),
        daemon=False,
    )
    started = False
    try:
        process.start()
        started = True
        child.close()
        if not parent.poll(timeout):
            raise ImageRejected("FILE_IMAGE_TOO_LARGE")
        try:
            frame = parent.recv_bytes(maxlength=_MAX_FRAME)
        except EOFError:
            raise ImageRejected("FILE_MALFORMED") from None
        except OSError as exc:
            if "bad message length" in str(exc):
                raise ImageRejected("FILE_TOO_LARGE") from None
            raise ImageRejected("FILE_MALFORMED") from None
        if not isinstance(frame, bytes):
            raise ImageRejected("FILE_MALFORMED")
        return _parse_frame(frame)
    finally:
        child.close()
        parent.close()
        if started:
            _reap(process)


def canonicalize_worker(data: bytes, declared_type: str, connection: Connection) -> None:
    """Top-level spawn target. Sends one bounded frame and never an exception object."""

    frame = _rejected_frame("FILE_MALFORMED")
    try:
        if not isinstance(data, bytes) or len(data) > MAX_UPLOAD_BYTES:
            code = "FILE_TOO_LARGE" if isinstance(data, bytes) else "FILE_MALFORMED"
            frame = _rejected_frame(code)
        else:
            image = canonicalize(data, declared_type)
            frame = _ok_frame(image.content_type, image.body)
    except ImageRejected as exc:
        frame = _rejected_frame(exc.code)
    except Exception:
        frame = _rejected_frame("FILE_MALFORMED")
    try:
        connection.send_bytes(frame)
    except Exception:
        return
    finally:
        connection.close()


def _ok_frame(content_type: str, body: bytes) -> bytes:
    if len(body) > MAX_UPLOAD_BYTES:
        return _rejected_frame("FILE_TOO_LARGE")
    if len(body) < 1:
        return _rejected_frame("FILE_EMPTY")
    label = content_type.encode("ascii", errors="strict")
    if len(label) > _MAX_LABEL or content_type not in ALLOWED_CONTENT_TYPES:
        return _rejected_frame("FILE_TYPE_MISMATCH")
    return b"O" + bytes((len(label),)) + label + body


def _rejected_frame(code: str) -> bytes:
    if code not in _STABLE_CODES:
        code = "FILE_MALFORMED"
    label = code.encode("ascii")
    return b"R" + bytes((len(label),)) + label


def _parse_frame(frame: bytes) -> CanonicalImage:
    if len(frame) < 2 or len(frame) > _MAX_FRAME:
        raise ImageRejected("FILE_MALFORMED")
    kind = frame[:1]
    length = frame[1]
    if length > _MAX_LABEL or len(frame) < 2 + length:
        raise ImageRejected("FILE_MALFORMED")
    try:
        label = frame[2 : 2 + length].decode("ascii")
    except UnicodeDecodeError:
        raise ImageRejected("FILE_MALFORMED") from None
    body = frame[2 + length :]
    if kind == b"R":
        if body or label not in _STABLE_CODES:
            raise ImageRejected("FILE_MALFORMED")
        raise ImageRejected(label)
    if kind != b"O":
        raise ImageRejected("FILE_MALFORMED")
    if label not in ALLOWED_CONTENT_TYPES:
        raise ImageRejected("FILE_TYPE_MISMATCH")
    if not body:
        raise ImageRejected("FILE_EMPTY")
    if len(body) > MAX_UPLOAD_BYTES:
        raise ImageRejected("FILE_TOO_LARGE")
    return CanonicalImage(content_type=label, body=bytes(body))


def _reap(process: BaseProcess) -> None:
    if process.is_alive():
        process.terminate()
    process.join(timeout=_JOIN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(timeout=_JOIN_SECONDS)

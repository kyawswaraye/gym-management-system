from __future__ import annotations

import socket
import struct
import time
from enum import StrEnum

from django.conf import settings

from apps.files.constants import MAX_UPLOAD_BYTES, SCAN_TIMEOUT_SECONDS
from apps.files.exceptions import ScannerUnavailable

_CHUNK = 65_536
_RESPONSE_LIMIT = 4096


class ScanVerdict(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"


class ClamAVScanner:
    def __init__(self, *, host: str, port: int, timeout_seconds: float) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> ClamAVScanner:
        return cls(
            host=str(settings.CLAMAV_HOST),
            port=int(settings.CLAMAV_PORT),
            timeout_seconds=float(settings.CLAMAV_TIMEOUT_SECONDS),
        )

    def scan(self, data: bytes) -> ScanVerdict:
        if not self.host or len(data) > MAX_UPLOAD_BYTES:
            raise ScannerUnavailable
        deadline = time.monotonic() + self.timeout_seconds
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self._remaining(deadline)
            ) as sock:
                self._send_instream(sock, data, deadline)
                text = self._read_status(sock, deadline)
        except (TimeoutError, OSError):
            raise ScannerUnavailable from None
        if text.endswith(" OK"):
            return ScanVerdict.CLEAN
        if text.endswith(" FOUND"):
            return ScanVerdict.INFECTED
        raise ScannerUnavailable

    def _send_instream(self, sock: socket.socket, data: bytes, deadline: float) -> None:
        self._send(sock, b"zINSTREAM\0", deadline)
        view = memoryview(data)
        for offset in range(0, len(data), _CHUNK):
            piece = view[offset : offset + _CHUNK]
            self._send(sock, struct.pack("!I", len(piece)) + piece, deadline)
        self._send(sock, struct.pack("!I", 0), deadline)

    def _read_status(self, sock: socket.socket, deadline: float) -> str:
        chunks: list[bytes] = []
        size = 0
        while size < _RESPONSE_LIMIT:
            sock.settimeout(self._remaining(deadline))
            packet = sock.recv(_RESPONSE_LIMIT - size)
            if not packet:
                break
            chunks.append(packet)
            size += len(packet)
            if b"\0" in packet:
                break
        raw = b"".join(chunks).split(b"\0", 1)[0]
        return raw.decode("ascii", "replace")

    def _send(self, sock: socket.socket, payload: bytes, deadline: float) -> None:
        sock.settimeout(self._remaining(deadline))
        sock.sendall(payload)

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ScannerUnavailable
        return remaining


def scan_timeout_seconds() -> float:
    return float(getattr(settings, "CLAMAV_TIMEOUT_SECONDS", SCAN_TIMEOUT_SECONDS))

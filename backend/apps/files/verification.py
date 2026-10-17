from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from apps.files.constants import MAX_UPLOAD_BYTES
from apps.files.exceptions import (
    ImageRejected,
    ObjectMissing,
    ObjectStoreUnavailable,
    ObjectTooLarge,
    ScannerUnavailable,
)
from apps.files.images import CanonicalImage
from apps.files.scanner import ScanVerdict
from apps.files.storage import StoredObject

_META_SHA = "x-amz-meta-gms-sha256"
_META_TYPE = "x-amz-meta-gms-content-type"
_META_SIZE = "x-amz-meta-gms-byte-size"


class VerifyOutcome(StrEnum):
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    STORE_ERROR = "STORE_ERROR"
    SCAN_ERROR = "SCAN_ERROR"
    MISSING = "MISSING"
    ABORTED = "ABORTED"


@dataclass(frozen=True)
class Verdict:
    outcome: VerifyOutcome
    code: str = ""
    content_type: str = ""
    byte_size: int = 0
    sha256: str = ""
    wrote_canonical: bool = False


class ObjectReader(Protocol):
    def read(self, object_key: str) -> StoredObject: ...

    def put_canonical(self, object_key: str, body: bytes, *, content_type: str) -> None: ...


class ByteScanner(Protocol):
    def scan(self, data: bytes) -> ScanVerdict: ...


def verify_upload(
    *,
    store: ObjectReader,
    scanner: ByteScanner,
    canonicalize: Callable[[bytes, str], CanonicalImage],
    object_key: str,
    upload_sha256: str,
    upload_byte_size: int,
    upload_content_type: str,
    allow_overwrite: Callable[[], bool] | None = None,
    release_unclaimed_object: Callable[[], None] | None = None,
) -> Verdict:
    """Scan and sanitize one object without holding a database transaction.

    ``canonicalize`` must already enforce its own resource bound. This function
    does not start a thread. ``wrote_canonical`` is true only after the canonical
    PUT returns. When that PUT lands and ``allow_overwrite`` then fails,
    ``release_unclaimed_object`` may remove the object. The callback must not
    delete an object that a current matching claim still owns.
    """

    try:
        stored = store.read(object_key)
    except ObjectMissing:
        return Verdict(VerifyOutcome.MISSING)
    except ObjectTooLarge:
        return Verdict(VerifyOutcome.REJECTED, code="FILE_TOO_LARGE")
    except ObjectStoreUnavailable:
        return Verdict(VerifyOutcome.STORE_ERROR, code="FILE_STORE_FAILED")
    size = len(stored.body)
    if size < 1:
        return Verdict(VerifyOutcome.REJECTED, code="FILE_EMPTY")
    if size > MAX_UPLOAD_BYTES:
        return Verdict(VerifyOutcome.REJECTED, code="FILE_TOO_LARGE")
    digest = hashlib.sha256(stored.body).hexdigest()
    if digest == upload_sha256 and size == upload_byte_size:
        return _sanitize_original(
            store=store,
            scanner=scanner,
            canonicalize=canonicalize,
            object_key=object_key,
            stored=stored,
            upload_content_type=upload_content_type,
            allow_overwrite=allow_overwrite,
            release_unclaimed_object=release_unclaimed_object,
        )
    return _recover_canonical(
        scanner=scanner,
        canonicalize=canonicalize,
        stored=stored,
        upload_content_type=upload_content_type,
    )


def _sanitize_original(
    *,
    store: ObjectReader,
    scanner: ByteScanner,
    canonicalize: Callable[[bytes, str], CanonicalImage],
    object_key: str,
    stored: StoredObject,
    upload_content_type: str,
    allow_overwrite: Callable[[], bool] | None,
    release_unclaimed_object: Callable[[], None] | None,
) -> Verdict:
    scanned = _scan(scanner, stored.body)
    if scanned is not None:
        return scanned
    try:
        canonical = _decode(canonicalize, stored.body, upload_content_type)
    except ImageRejected as exc:
        return Verdict(VerifyOutcome.REJECTED, code=exc.code)
    if not 1 <= len(canonical.body) <= MAX_UPLOAD_BYTES:
        code = "FILE_EMPTY" if len(canonical.body) < 1 else "FILE_TOO_LARGE"
        return Verdict(VerifyOutcome.REJECTED, code=code)
    if allow_overwrite is not None and not allow_overwrite():
        return Verdict(VerifyOutcome.ABORTED)
    try:
        store.put_canonical(object_key, canonical.body, content_type=canonical.content_type)
    except (ObjectMissing, ObjectStoreUnavailable, ObjectTooLarge):
        return Verdict(VerifyOutcome.STORE_ERROR, code="FILE_STORE_FAILED")
    if allow_overwrite is not None and not allow_overwrite():
        if release_unclaimed_object is not None:
            release_unclaimed_object()
        return Verdict(VerifyOutcome.ABORTED, wrote_canonical=True)
    try:
        read_back = store.read(object_key)
    except (ObjectMissing, ObjectStoreUnavailable, ObjectTooLarge):
        return Verdict(VerifyOutcome.STORE_ERROR, code="FILE_STORE_FAILED", wrote_canonical=True)
    if read_back.body != canonical.body or not _server_metadata(
        read_back, content_type=canonical.content_type
    ):
        return Verdict(VerifyOutcome.STORE_ERROR, code="FILE_STORE_FAILED", wrote_canonical=True)
    scanned = _scan(scanner, read_back.body)
    if scanned is not None:
        return Verdict(outcome=scanned.outcome, code=scanned.code, wrote_canonical=True)
    return _clean(read_back.body, canonical.content_type, wrote_canonical=True)


def _recover_canonical(
    *,
    scanner: ByteScanner,
    canonicalize: Callable[[bytes, str], CanonicalImage],
    stored: StoredObject,
    upload_content_type: str,
) -> Verdict:
    try:
        canonical = _decode(canonicalize, stored.body, upload_content_type)
    except ImageRejected:
        return Verdict(VerifyOutcome.REJECTED, code="FILE_CHECKSUM_MISMATCH")
    if (
        canonical.body != stored.body
        or canonical.content_type != upload_content_type
        or not _server_metadata(stored, content_type=canonical.content_type)
    ):
        return Verdict(VerifyOutcome.REJECTED, code="FILE_CHECKSUM_MISMATCH")
    scanned = _scan(scanner, stored.body)
    if scanned is not None:
        return scanned
    return _clean(stored.body, canonical.content_type)


def _decode(
    canonicalize: Callable[[bytes, str], CanonicalImage],
    data: bytes,
    content_type: str,
) -> CanonicalImage:
    try:
        return canonicalize(data, content_type)
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected("FILE_MALFORMED") from None


def _scan(scanner: ByteScanner, data: bytes) -> Verdict | None:
    try:
        verdict = scanner.scan(data)
    except ScannerUnavailable:
        return Verdict(VerifyOutcome.SCAN_ERROR, code="FILE_SCAN_UNAVAILABLE")
    if verdict is ScanVerdict.INFECTED:
        return Verdict(VerifyOutcome.REJECTED, code="FILE_INFECTED")
    return None


def _server_metadata(stored: StoredObject, *, content_type: str) -> bool:
    digest = hashlib.sha256(stored.body).hexdigest()
    metadata = {key.lower(): value for key, value in stored.metadata.items()}
    return (
        metadata.get(_META_SHA) == digest
        and metadata.get(_META_TYPE) == content_type
        and metadata.get(_META_SIZE) == str(len(stored.body))
    )


def _clean(body: bytes, content_type: str, *, wrote_canonical: bool = False) -> Verdict:
    return Verdict(
        outcome=VerifyOutcome.CLEAN,
        content_type=content_type,
        byte_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        wrote_canonical=wrote_canonical,
    )

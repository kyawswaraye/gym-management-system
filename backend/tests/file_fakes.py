from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.db import connection

from apps.files.exceptions import (
    ObjectMissing,
    ObjectStatInvalid,
    ObjectStoreUnavailable,
    ScannerUnavailable,
)
from apps.files.images import CanonicalImage
from apps.files.scanner import ScanVerdict
from apps.files.storage import ObjectStat, PresignedGet, PresignedPost, StoredObject

CURRENT: dict[str, object] = {}


@dataclass
class MemoryStore:
    objects: dict[str, StoredObject] = field(default_factory=dict)
    fail: str = ""
    puts: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    atomic_during_read: list[bool] = field(default_factory=list)
    atomic_depth_during_presign: list[int] = field(default_factory=list)
    on_read: Callable[[], None] | None = None
    before_put: Callable[[], None] | None = None
    on_failed_delete: Callable[[], None] | None = None

    def presign_post(
        self, *, object_key: str, content_type: str, moment: datetime
    ) -> PresignedPost:
        self.atomic_depth_during_presign.append(len(connection.atomic_blocks))
        if self.fail == "presign":
            raise ObjectStoreUnavailable
        expires = moment.astimezone(UTC) + timedelta(seconds=300)
        return PresignedPost(
            url="https://files.example.com/gym-private",
            expires_at=expires,
            fields={
                "key": object_key,
                "Content-Type": content_type,
                "policy": "cG9saWN5",
                "x-amz-algorithm": "AWS4-HMAC-SHA256",
                "x-amz-credential": "local-access/20261017/us-east-1/s3/aws4_request",
                "x-amz-date": moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "x-amz-signature": "ab" * 32,
                "success_action_status": "204",
            },
        )

    def presign_get(
        self, *, object_key: str, content_type: str, filename: str, moment: datetime
    ) -> PresignedGet:
        expires = moment.astimezone(UTC) + timedelta(seconds=60)
        return PresignedGet(
            url=(
                "https://files.example.com/gym-private/"
                f"{object_key}?response-content-type={content_type}"
                f"&response-content-disposition=inline%3B%20filename%3D%22{filename}%22"
                "&response-cache-control=private%2C%20no-store&X-Amz-Expires=60"
            ),
            expires_at=expires,
        )

    def head(self, object_key: str) -> ObjectStat:
        if self.fail == "head":
            raise ObjectStoreUnavailable
        if self.fail == "head-stat":
            raise ObjectStatInvalid
        stored = self.objects.get(object_key)
        if stored is None:
            raise ObjectMissing
        return ObjectStat(content_length=len(stored.body), metadata=stored.metadata)

    def read(self, object_key: str) -> StoredObject:
        self.atomic_during_read.append(connection.in_atomic_block)
        if self.on_read is not None:
            self.on_read()
        if self.fail == "read":
            raise ObjectStoreUnavailable
        stored = self.objects.get(object_key)
        if stored is None:
            raise ObjectMissing
        return stored

    def put_canonical(self, object_key: str, body: bytes, *, content_type: str) -> None:
        if self.fail == "put":
            raise ObjectStoreUnavailable
        callback = self.before_put
        if callback is not None:
            self.before_put = None
            callback()
        import hashlib

        digest = hashlib.sha256(body).hexdigest()
        self.puts.append(object_key)
        self.objects[object_key] = StoredObject(
            body=body,
            metadata={
                "x-amz-meta-gms-sha256": digest,
                "x-amz-meta-gms-content-type": content_type,
                "x-amz-meta-gms-byte-size": str(len(body)),
            },
        )

    def delete(self, object_key: str) -> None:
        if self.fail == "delete":
            callback = self.on_failed_delete
            if callback is not None:
                self.on_failed_delete = None
                callback()
            raise ObjectStoreUnavailable
        self.deletes.append(object_key)
        self.objects.pop(object_key, None)


class MemoryScanner:
    def __init__(self, verdict: ScanVerdict = ScanVerdict.CLEAN, *, fail: bool = False) -> None:
        self.verdict = verdict
        self.fail = fail
        self.calls = 0

    def scan(self, data: bytes) -> ScanVerdict:
        self.calls += 1
        if self.fail:
            raise ScannerUnavailable
        if data.startswith(b"infected"):
            return ScanVerdict.INFECTED
        return self.verdict


def build_store() -> MemoryStore:
    store = CURRENT.get("store")
    if not isinstance(store, MemoryStore):
        raise RuntimeError("file test store is not configured")
    return store


def build_scanner() -> MemoryScanner:
    scanner = CURRENT.get("scanner")
    if not isinstance(scanner, MemoryScanner):
        raise RuntimeError("file test scanner is not configured")
    return scanner


def build_processor() -> Callable[[bytes, str], CanonicalImage]:
    processor = CURRENT.get("processor")
    if not callable(processor):
        raise RuntimeError("file test processor is not configured")
    return processor


def use_fakes(
    store: MemoryStore | None = None,
    scanner: MemoryScanner | None = None,
    processor: Callable[[bytes, str], CanonicalImage] | None = None,
) -> None:
    CURRENT["store"] = store or MemoryStore()
    CURRENT["scanner"] = scanner or MemoryScanner()
    CURRENT["processor"] = processor or _echo


def _echo(data: bytes, content_type: str) -> CanonicalImage:
    return CanonicalImage(content_type=content_type, body=data)

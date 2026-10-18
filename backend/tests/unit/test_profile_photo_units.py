from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from PIL import Image
from rest_framework.exceptions import ErrorDetail

from apps.files.constants import MAX_UPLOAD_BYTES
from apps.files.exceptions import (
    FileRequestError,
    ImageRejected,
    ObjectMissing,
    ObjectStatInvalid,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from apps.files.images import canonicalize, sniff_content_type
from apps.files.scanner import ClamAVScanner, ScanVerdict
from apps.files.serializers import (
    CompleteUploadSerializer,
    UploadIntentSerializer,
    validated_file_data,
)
from apps.files.signing import (
    canonical_request,
    hex_signature,
    host_header,
    signing_key,
    string_to_sign,
)
from apps.files.storage import MinioObjectStore, _nonnegative_content_length
from apps.files.verification import VerifyOutcome, verify_upload
from apps.files.views import (
    CompleteUploadView,
    DeleteUploadView,
    DownloadUrlView,
    UploadIntentView,
)
from tests.file_fakes import MemoryScanner, MemoryStore

MOMENT = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
SHA = "ab" * 32


def _png(*color: int, size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _intent(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "original_name": " portrait.PNG ",
        "content_type": "image/png",
        "byte_size": 128,
        "sha256": SHA,
        "purpose": "PROFILE_PHOTO",
        "owner_type": "MEMBER",
        "owner_id": str(uuid4()),
    }
    body.update(overrides)
    return body


def _store() -> MinioObjectStore:
    return MinioObjectStore(
        endpoint_url="http://minio:9000",
        public_url="https://files.example.com",
        bucket="gym-private",
        region="us-east-1",
        access_key="local-access",
        secret_key="local-secret-value",
        timeout_seconds=5,
    )


def test_upload_intent_fields_use_stable_codes() -> None:
    valid = UploadIntentSerializer(data=_intent())
    assert validated_file_data(valid, tuple(_intent()))["original_name"] == "portrait.PNG"
    cases = {
        "purpose": ("DOCUMENT", 422, "UNSUPPORTED_FILE_PURPOSE"),
        "owner_type": ("STAFF", 422, "UNSUPPORTED_FILE_OWNER"),
        "content_type": ("image/gif", 422, "FILE_TYPE_NOT_ALLOWED"),
        "sha256": ("AB" * 32, 422, "FILE_CHECKSUM_INVALID"),
    }
    for field, (value, status, code) in cases.items():
        with pytest.raises(Exception) as caught:
            validated_file_data(
                UploadIntentSerializer(data=_intent(**{field: value})), tuple(_intent())
            )
        assert caught.value.status_code == status
        assert caught.value.detail.code == code
        assert field in caught.value.error_fields
    empty = UploadIntentSerializer(data=_intent(byte_size=0))
    large = UploadIntentSerializer(data=_intent(byte_size=MAX_UPLOAD_BYTES + 1))
    named = UploadIntentSerializer(data=_intent(original_name="bad/name"))
    unknown = UploadIntentSerializer(data=_intent(gym_id=str(uuid4())))
    with pytest.raises(Exception) as empty_error:
        validated_file_data(empty, tuple(_intent()))
    with pytest.raises(Exception) as large_error:
        validated_file_data(large, tuple(_intent()))
    with pytest.raises(Exception) as name_error:
        validated_file_data(named, tuple(_intent()))
    with pytest.raises(Exception) as unknown_error:
        validated_file_data(unknown, tuple(_intent()))
    assert empty_error.value.detail.code == "FILE_EMPTY"
    assert large_error.value.detail.code == "FILE_TOO_LARGE"
    assert name_error.value.status_code == 422
    assert name_error.value.detail.code == "INVALID_REQUEST"
    assert unknown_error.value.status_code == 400
    assert unknown_error.value.detail.code == "INVALID_REQUEST"
    assert "gym_id" in unknown_error.value.error_fields
    assert isinstance(name_error.value.detail, ErrorDetail)


def test_complete_body_is_only_the_original_declaration() -> None:
    payload = validated_file_data(
        CompleteUploadSerializer(data={"sha256": SHA, "byte_size": 12}), ("sha256", "byte_size")
    )
    assert payload == {"sha256": SHA, "byte_size": 12}
    with pytest.raises(Exception) as extra:
        validated_file_data(
            CompleteUploadSerializer(data={"sha256": SHA, "byte_size": 12, "etag": "abc"}),
            ("sha256", "byte_size"),
        )
    assert extra.value.status_code == 400


def test_presigned_post_policy_is_exact_and_omits_the_secret() -> None:
    signed = _store().presign_post(object_key="gym/object", content_type="image/png", moment=MOMENT)
    policy = json.loads(base64.b64decode(signed.fields["policy"]))
    rendered = json.dumps(signed.fields)

    assert signed.url == "https://files.example.com/gym-private"
    assert "minio:9000" not in signed.url
    assert signed.expires_at.isoformat() == "2026-10-17T10:05:00+00:00"
    assert policy["expiration"] == "2026-10-17T10:05:00Z"
    assert policy["conditions"] == [
        {"bucket": "gym-private"},
        ["eq", "$key", "gym/object"],
        ["eq", "$Content-Type", "image/png"],
        ["content-length-range", 1, 5242880],
        ["eq", "$success_action_status", "204"],
        ["eq", "$x-amz-algorithm", "AWS4-HMAC-SHA256"],
        ["eq", "$x-amz-credential", "local-access/20261017/us-east-1/s3/aws4_request"],
        ["eq", "$x-amz-date", "20261017T100000Z"],
    ]
    exempt = {"policy", "x-amz-signature"}
    exact: dict[str, str] = {}
    for condition in policy["conditions"]:
        if isinstance(condition, list) and condition[0] == "eq":
            assert condition[1].startswith("$")
            exact[condition[1][1:]] = condition[2]
    for name, value in signed.fields.items():
        if name in exempt:
            continue
        assert exact[name] == value
    assert exact["x-amz-algorithm"] == signed.fields["x-amz-algorithm"]
    assert exact["x-amz-credential"] == signed.fields["x-amz-credential"]
    assert exact["x-amz-date"] == signed.fields["x-amz-date"]
    assert "file" not in signed.fields
    assert signed.fields["key"] == "gym/object"
    assert signed.fields["success_action_status"] == "204"
    assert "x-amz-security-token" not in signed.fields
    assert "local-secret-value" not in rendered
    assert "local-secret-value" not in signed.fields["x-amz-credential"]
    assert len(signed.fields["x-amz-signature"]) == 64


def test_presigned_get_uses_the_public_origin_and_fixed_filename() -> None:
    signed = _store().presign_get(
        object_key="gym/object",
        content_type="image/webp",
        filename="profile-photo.webp",
        moment=MOMENT,
    )
    assert signed.url.startswith("https://files.example.com/gym-private/gym/object?")
    assert "minio:9000" not in signed.url
    assert "local-secret-value" not in signed.url
    disposition = "response-content-disposition=inline%3B%20filename%3D%22profile-photo.webp%22"
    assert "response-content-type=image%2Fwebp" in signed.url
    assert disposition in signed.url
    assert "attachment" not in signed.url
    assert "profile-photo.webp" in signed.url
    assert "response-cache-control=private%2C%20no-store" in signed.url
    assert "X-Amz-Expires=60" in signed.url
    assert signed.expires_at.isoformat() == "2026-10-17T10:01:00+00:00"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://files.example.com/gym-private/key", "files.example.com"),
        ("https://files.example.com:443/gym-private/key", "files.example.com"),
        ("https://files.example.com:8443/gym-private/key", "files.example.com:8443"),
        ("https://Files.Example.COM:80/gym-private/key", "files.example.com:80"),
        ("http://files.localhost/gym-private/key", "files.localhost"),
        ("http://files.localhost:80/gym-private/key", "files.localhost"),
        ("http://files.localhost:18088/gym-private/key", "files.localhost:18088"),
        ("http://files.localhost:443/gym-private/key", "files.localhost:443"),
        ("http://127.0.0.1/gym-private/key", "127.0.0.1"),
        ("http://127.0.0.1:80/gym-private/key", "127.0.0.1"),
        ("http://127.0.0.1:19000/gym-private/key", "127.0.0.1:19000"),
        ("https://127.0.0.1/gym-private/key", "127.0.0.1"),
        ("https://127.0.0.1:443/gym-private/key", "127.0.0.1"),
        ("https://192.0.2.10:9000/gym-private/key", "192.0.2.10:9000"),
        ("http://[2001:db8::1]/gym-private/key", "[2001:db8::1]"),
        ("http://[2001:db8::1]:80/gym-private/key", "[2001:db8::1]"),
        ("https://[2001:db8::1]/gym-private/key", "[2001:db8::1]"),
        ("https://[2001:DB8::1]:443/gym-private/key", "[2001:db8::1]"),
        ("http://[2001:db8::1]:9000/gym-private/key", "[2001:db8::1]:9000"),
        ("https://[::1]:8443/gym-private/key", "[::1]:8443"),
        ("http://user:secret@[2001:db8::1]:9000/gym-private/key", "[2001:db8::1]:9000"),
    ],
)
def test_host_header_keeps_only_nondefault_ports(url: str, expected: str) -> None:
    assert host_header(url) == expected


def _presign_signature_matches(url: str, *, secret: str, region: str, host: str) -> bool:
    base, signature = url.split("&X-Amz-Signature=", 1)
    parts = urlsplit(base)
    query = dict(item.split("=", 1) for item in parts.query.split("&"))
    amz_date = query["X-Amz-Date"]
    scope = unquote(query["X-Amz-Credential"]).split("/", 1)[1]
    request = canonical_request(
        method="GET",
        path=parts.path,
        query=parts.query,
        headers={"host": host},
        signed_headers="host",
        payload_hash="UNSIGNED-PAYLOAD",
    )
    expected = hex_signature(
        key=signing_key(secret=secret, date_stamp=amz_date[:8], region=region),
        message=string_to_sign(amz_date=amz_date, credential_scope=scope, request=request),
    )
    return signature == expected


def test_presigned_get_signs_public_authority_with_nondefault_port() -> None:
    secret = "local-secret-value"
    region = "us-east-1"
    default_https = _store().presign_get(
        object_key="gym/object",
        content_type="image/webp",
        filename="profile-photo.webp",
        moment=MOMENT,
    )
    assert _presign_signature_matches(
        default_https.url, secret=secret, region=region, host="files.example.com"
    )
    assert not _presign_signature_matches(
        default_https.url, secret=secret, region=region, host="files.example.com:443"
    )

    proxied = MinioObjectStore(
        endpoint_url="http://127.0.0.1:19000",
        public_url="http://files.localhost:18088",
        bucket="gym-private",
        region=region,
        access_key="local-access",
        secret_key=secret,
        timeout_seconds=5,
    ).presign_get(
        object_key="gym/object",
        content_type="image/webp",
        filename="profile-photo.webp",
        moment=MOMENT,
    )
    assert proxied.url.startswith("http://files.localhost:18088/gym-private/gym/object?")
    assert _presign_signature_matches(
        proxied.url, secret=secret, region=region, host="files.localhost:18088"
    )
    assert not _presign_signature_matches(
        proxied.url, secret=secret, region=region, host="files.localhost"
    )

    ipv6 = MinioObjectStore(
        endpoint_url="http://[2001:db8::1]:9000",
        public_url="https://[2001:db8::1]:8443",
        bucket="gym-private",
        region=region,
        access_key="local-access",
        secret_key=secret,
        timeout_seconds=5,
    ).presign_get(
        object_key="gym/object",
        content_type="image/webp",
        filename="profile-photo.webp",
        moment=MOMENT,
    )
    assert _presign_signature_matches(
        ipv6.url, secret=secret, region=region, host="[2001:db8::1]:8443"
    )
    assert not _presign_signature_matches(
        ipv6.url, secret=secret, region=region, host="[2001:db8::1]"
    )


def _assert_endpoint_request_signs_authority(bind_host: str, family: socket.AddressFamily) -> None:
    server = socket.socket(family, socket.SOCK_STREAM)
    if family is socket.AF_INET6:
        server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    server.bind((bind_host, 0))
    server.listen(1)
    server.settimeout(5)
    port = server.getsockname()[1]
    captured: list[bytes] = []

    def serve() -> None:
        connection, _address = server.accept()
        with connection:
            data = b""
            while b"\r\n\r\n" not in data:
                packet = connection.recv(4096)
                if not packet:
                    break
                data += packet
            captured.append(data)
            connection.sendall(
                b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    bracketed = f"[{bind_host}]" if ":" in bind_host else bind_host
    endpoint = f"http://{bracketed}:{port}"
    store = MinioObjectStore(
        endpoint_url=endpoint,
        public_url="https://files.example.com",
        bucket="gym-private",
        region="us-east-1",
        access_key="local-access",
        secret_key="local-secret-value",
        timeout_seconds=2,
    )
    try:
        with pytest.raises(ObjectMissing):
            store.head("gym/object")
    finally:
        thread.join(timeout=2)
        server.close()
    assert captured, "endpoint request was not received"
    lines = captured[0].split(b"\r\n\r\n", 1)[0].decode().split("\r\n")
    assert lines[0].startswith("HEAD /gym-private/gym/object ")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    expected_host = f"{bracketed}:{port}"
    assert headers["host"] == expected_host
    authorization = headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 ")
    fields = dict(
        item.split("=", 1) for item in authorization.removeprefix("AWS4-HMAC-SHA256 ").split(", ")
    )
    signed_headers = fields["SignedHeaders"]
    signed = {name: headers[name] for name in signed_headers.split(";")}
    scope = fields["Credential"].split("/", 1)[1]
    date_stamp = scope.split("/", 1)[0]
    request = canonical_request(
        method="HEAD",
        path="/gym-private/gym/object",
        query="",
        headers=signed,
        signed_headers=signed_headers,
        payload_hash=signed["x-amz-content-sha256"],
    )
    expected = hex_signature(
        key=signing_key(secret="local-secret-value", date_stamp=date_stamp, region="us-east-1"),
        message=string_to_sign(
            amz_date=signed["x-amz-date"], credential_scope=scope, request=request
        ),
    )
    assert fields["Signature"] == expected
    without_port = dict(signed)
    without_port["host"] = bracketed
    unsigned = canonical_request(
        method="HEAD",
        path="/gym-private/gym/object",
        query="",
        headers=without_port,
        signed_headers=signed_headers,
        payload_hash=signed["x-amz-content-sha256"],
    )
    rejected = hex_signature(
        key=signing_key(secret="local-secret-value", date_stamp=date_stamp, region="us-east-1"),
        message=string_to_sign(
            amz_date=signed["x-amz-date"], credential_scope=scope, request=unsigned
        ),
    )
    assert rejected != expected


def test_server_endpoint_request_signs_ipv4_authority_with_port() -> None:
    _assert_endpoint_request_signs_authority("127.0.0.1", socket.AF_INET)


def test_server_endpoint_request_signs_ipv6_authority_with_port() -> None:
    _assert_endpoint_request_signs_authority("::1", socket.AF_INET6)


def test_object_reads_stop_at_the_declared_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.files.storage.MAX_UPLOAD_BYTES", 4)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve(status: bytes) -> None:
        connection, _address = server.accept()
        with connection:
            data = b""
            while b"\r\n\r\n" not in data:
                packet = connection.recv(4096)
                if not packet:
                    break
                data += packet
            connection.sendall(status)

    oversized = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nx-amz-meta-gms-sha256: ab\r\n\r\nhello"
    thread = threading.Thread(target=serve, args=(oversized,), daemon=True)
    thread.start()
    store = MinioObjectStore(
        endpoint_url=f"http://127.0.0.1:{port}",
        public_url="https://files.example.com",
        bucket="gym-private",
        region="us-east-1",
        access_key="local-access",
        secret_key="local-secret-value",
        timeout_seconds=2,
    )
    with pytest.raises(ObjectTooLarge):
        store.read("gym/object")
    thread.join(timeout=2)
    server.close()


def test_file_views_have_no_outer_atomic_request() -> None:
    from django.core.handlers.base import BaseHandler

    handler = BaseHandler()
    for view in (UploadIntentView, CompleteUploadView, DownloadUrlView, DeleteUploadView):
        callback = view.as_view()
        assert "default" in callback._non_atomic_requests
        assert handler.make_view_atomic(callback) is callback


def test_retry_after_header_uses_integer_wait_only() -> None:
    from apps.common.api.exceptions import api_exception_handler

    class _Request:
        request_id = "req_retry"

    def respond(wait: object | None):
        error = FileRequestError(
            code="FILE_SCAN_IN_PROGRESS",
            message="A file scan is already in progress.",
            status_code=409,
        )
        if wait is not None:
            error.wait = wait
        return api_exception_handler(error, {"request": _Request()})

    present = respond(17)
    assert present.status_code == 409
    assert present["Retry-After"] == "17"
    assert present.data["error"]["code"] == "FILE_SCAN_IN_PROGRESS"
    absent = respond(None)
    assert "Retry-After" not in absent
    negative = respond(-4)
    assert "Retry-After" not in negative
    flagged = respond(True)
    assert "Retry-After" not in flagged


def test_successful_head_with_invalid_length_is_an_integrity_error() -> None:
    for raw in (None, "", "-1", "1.5", "no", " 12", "+3", "1" * 21):
        with pytest.raises(ObjectStatInvalid):
            _nonnegative_content_length(raw)
    assert _nonnegative_content_length("0") == 0
    assert _nonnegative_content_length("12") == 12

    def head_of(status_line: bytes) -> object:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve() -> None:
            connection, _address = server.accept()
            with connection:
                data = b""
                while b"\r\n\r\n" not in data:
                    packet = connection.recv(4096)
                    if not packet:
                        break
                    data += packet
                connection.sendall(status_line)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        store = MinioObjectStore(
            endpoint_url=f"http://127.0.0.1:{port}",
            public_url="https://files.example.com",
            bucket="gym-private",
            region="us-east-1",
            access_key="local-access",
            secret_key="local-secret-value",
            timeout_seconds=2,
        )
        try:
            return store.head("gym/object")
        finally:
            thread.join(timeout=2)
            server.close()

    valid = head_of(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\n")
    assert valid.content_length == 12
    for status_line in (
        b"HTTP/1.1 200 OK\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 1.5\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: no\r\n\r\n",
    ):
        with pytest.raises(ObjectStatInvalid):
            head_of(status_line)
    with pytest.raises(ObjectStoreUnavailable):
        head_of(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n")
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    offline = MinioObjectStore(
        endpoint_url=f"http://127.0.0.1:{port}",
        public_url="https://files.example.com",
        bucket="gym-private",
        region="us-east-1",
        access_key="local-access",
        secret_key="local-secret-value",
        timeout_seconds=1,
    )
    with pytest.raises(ObjectStoreUnavailable):
        offline.head("gym/object")


def test_canonicalize_strips_metadata_and_rejects_unsafe_images() -> None:
    original = _png(10, 20, 30)
    first = canonicalize(original, "image/png")
    second = canonicalize(original, "image/png")
    assert first.body == second.body
    assert sniff_content_type(first.body) == "image/png"
    with Image.open(BytesIO(first.body)) as cleaned:
        assert cleaned.size == (2, 2)
        assert "exif" not in cleaned.info
        assert "icc_profile" not in cleaned.info

    animated = BytesIO()
    frames = [Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")]
    frames[0].save(animated, format="WEBP", save_all=True, append_images=frames[1:], duration=10)
    with pytest.raises(ImageRejected) as animated_error:
        canonicalize(animated.getvalue(), "image/webp")
    assert animated_error.value.code == "FILE_MALFORMED"
    with pytest.raises(ImageRejected) as mismatch:
        canonicalize(original, "image/jpeg")
    assert mismatch.value.code == "FILE_TYPE_MISMATCH"
    with pytest.raises(ImageRejected) as pixels:
        canonicalize(_png(1, 2, 3, size=(4, 4)), "image/png", max_pixels=8)
    assert pixels.value.code == "FILE_IMAGE_TOO_LARGE"


def test_clamav_instream_maps_results_without_echoing_bytes() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seen: list[bytes] = []

    def serve(status: bytes) -> None:
        connection, _address = server.accept()
        with connection:
            data = b""

            def take(size: int) -> bytes:
                nonlocal data
                while len(data) < size:
                    packet = connection.recv(size - len(data))
                    if not packet:
                        break
                    data += packet
                piece, data = data[:size], data[size:]
                return piece

            header = take(10)
            body = b""
            while True:
                length = int.from_bytes(take(4), "big")
                if length == 0:
                    break
                body += take(length)
            seen.append(header + body)
            connection.sendall(status)

    thread = threading.Thread(target=serve, args=(b"stream: OK\0",), daemon=True)
    thread.start()
    verdict = ClamAVScanner(host="127.0.0.1", port=port, timeout_seconds=2).scan(b"png-bytes")
    thread.join(timeout=2)
    assert verdict is ScanVerdict.CLEAN
    assert b"zINSTREAM\0" in seen[0]
    assert b"png-bytes" in seen[0]

    thread = threading.Thread(target=serve, args=(b"stream: Eicar FOUND\0",), daemon=True)
    thread.start()
    infected = ClamAVScanner(host="127.0.0.1", port=port, timeout_seconds=2).scan(b"png-bytes")
    thread.join(timeout=2)
    assert infected is ScanVerdict.INFECTED
    server.close()


def test_verification_releases_a_put_that_loses_the_claim_after_the_prewrite_check() -> None:
    original = _png(4, 5, 6)
    digest = hashlib.sha256(original).hexdigest()
    from apps.files.storage import StoredObject

    store = MemoryStore()
    store.objects["gym/object"] = StoredObject(body=original, metadata={})
    checks = {"n": 0}
    released: list[str] = []

    def allow() -> bool:
        checks["n"] += 1
        return checks["n"] == 1

    def release() -> None:
        released.append("gym/object")
        store.delete("gym/object")

    lost = verify_upload(
        store=store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
        allow_overwrite=allow,
        release_unclaimed_object=release,
    )
    assert lost.outcome is VerifyOutcome.ABORTED
    assert lost.wrote_canonical is True
    assert checks["n"] == 2
    assert store.puts == ["gym/object"]
    assert released == ["gym/object"]
    assert "gym/object" not in store.objects

    owned = MemoryStore()
    owned.objects["gym/object"] = StoredObject(body=original, metadata={})

    def keep() -> None:
        released.append("owned")

    kept = verify_upload(
        store=owned,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
        allow_overwrite=lambda: True,
        release_unclaimed_object=keep,
    )
    assert kept.outcome is VerifyOutcome.CLEAN
    assert kept.wrote_canonical is True
    assert released == ["gym/object"]
    assert "gym/object" in owned.objects


def test_verification_covers_scan_decode_and_crash_recovery() -> None:
    original = _png(4, 5, 6)
    from apps.files.storage import StoredObject

    store = MemoryStore()
    digest = hashlib.sha256(original).hexdigest()
    store.objects["gym/object"] = StoredObject(body=original, metadata={})
    clean = verify_upload(
        store=store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
    )
    assert clean.outcome is VerifyOutcome.CLEAN
    assert clean.wrote_canonical is True
    assert clean.sha256 != digest
    assert store.atomic_during_read == [False, False]
    assert store.puts == ["gym/object"]

    store.objects["gym/object"] = StoredObject(body=b"infected-bytes", metadata={})
    infected = verify_upload(
        store=store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=hashlib.sha256(b"infected-bytes").hexdigest(),
        upload_byte_size=len(b"infected-bytes"),
        upload_content_type="image/png",
    )
    assert infected.code == "FILE_INFECTED"
    mismatched = verify_upload(
        store=store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256="cd" * 32,
        upload_byte_size=4,
        upload_content_type="image/png",
    )
    assert mismatched.code == "FILE_CHECKSUM_MISMATCH"
    unavailable = verify_upload(
        store=MemoryStore(),
        scanner=MemoryScanner(fail=True),
        canonicalize=canonicalize,
        object_key="missing",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
    )
    assert unavailable.outcome is VerifyOutcome.MISSING
    failing = MemoryStore(fail="read")
    failing.objects["gym/object"] = StoredObject(body=original, metadata={})
    stored = verify_upload(
        store=failing,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
    )
    assert stored.code == "FILE_STORE_FAILED"
    canonical = canonicalize(original, "image/png")
    recovered_store = MemoryStore()
    recovered_store.put_canonical("gym/object", canonical.body, content_type="image/png")
    recovered = verify_upload(
        store=recovered_store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
    )
    assert recovered.outcome is VerifyOutcome.CLEAN
    assert recovered.wrote_canonical is False
    assert recovered_store.puts == ["gym/object"]
    assert recovered_store.objects["gym/object"].body == canonical.body
    blocked = MemoryStore()
    blocked.objects["gym/object"] = StoredObject(body=original, metadata={})

    def release_without_a_write() -> None:
        raise AssertionError("released an object that was not overwritten")

    aborted = verify_upload(
        store=blocked,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        object_key="gym/object",
        upload_sha256=digest,
        upload_byte_size=len(original),
        upload_content_type="image/png",
        allow_overwrite=lambda: False,
        release_unclaimed_object=release_without_a_write,
    )
    assert aborted.outcome is VerifyOutcome.ABORTED
    assert aborted.wrote_canonical is False
    assert blocked.puts == []
    assert blocked.objects["gym/object"].body == original
    assert ObjectMissing.__name__
    assert ObjectStoreUnavailable.__name__


def _aws_store(**overrides: object) -> MinioObjectStore:
    values = dict(
        endpoint_url="https://s3.ap-southeast-1.amazonaws.com",
        public_url="https://s3.ap-southeast-1.amazonaws.com",
        bucket="gym-prod",
        region="ap-southeast-1",
        access_key="AKIAEXAMPLE",
        secret_key="aws-secret-value",
        timeout_seconds=5,
        addressing_style="virtual",
        session_token="",
    )
    values.update(overrides)
    return MinioObjectStore(**values)  # type: ignore[arg-type]


def test_virtual_hosted_presign_uses_bucket_host_and_optional_session_token() -> None:
    store = _aws_store(session_token="session-token-value")
    posted = store.presign_post(object_key="gym/object", content_type="image/png", moment=MOMENT)
    fetched = store.presign_get(
        object_key="gym/object",
        content_type="image/webp",
        filename="profile-photo.webp",
        moment=MOMENT,
    )
    policy = json.loads(base64.b64decode(posted.fields["policy"]))
    assert posted.url == "https://gym-prod.s3.ap-southeast-1.amazonaws.com"
    assert posted.fields["x-amz-security-token"] == "session-token-value"
    assert ["eq", "$x-amz-security-token", "session-token-value"] in policy["conditions"]
    assert fetched.url.startswith("https://gym-prod.s3.ap-southeast-1.amazonaws.com/gym/object?")
    assert "X-Amz-Security-Token=session-token-value" in fetched.url
    assert "/gym-prod/gym/object" not in fetched.url
    assert "aws-secret-value" not in fetched.url
    assert "aws-secret-value" not in json.dumps(posted.fields)


def test_path_style_minio_behavior_is_unchanged_without_a_session_token() -> None:
    signed = _store().presign_post(object_key="gym/object", content_type="image/png", moment=MOMENT)
    assert signed.url == "https://files.example.com/gym-private"
    assert "x-amz-security-token" not in signed.fields

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import quote, urlsplit


def signing_key(*, secret: str, date_stamp: str, region: str) -> bytes:
    def digest(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    dated = digest(f"AWS4{secret}".encode(), date_stamp)
    regional = digest(dated, region)
    service = digest(regional, "s3")
    return digest(service, "aws4_request")


def hex_signature(*, key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


_DEFAULT_PORTS = {"http": 80, "https": 443}


def host_header(url: str) -> str:
    """Return the SigV4 canonical Host value for ``url``.

    The value is the URL hostname, with an IPv6 address in brackets, plus a
    port only when that port is not the scheme default. HTTP omits 80 and
    HTTPS omits 443. Every other port stays, which is the authority a client
    sends and the host S3 verification recomputes.
    """

    parts = urlsplit(url)
    hostname = parts.hostname
    if not hostname:
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parts.port
    if port is None or port == _DEFAULT_PORTS.get(parts.scheme):
        return host
    return f"{host}:{port}"


def canonical_object_path(bucket: str, object_key: str) -> str:
    segments = [bucket, *object_key.split("/")]
    return "/" + "/".join(quote(segment, safe="-_.~") for segment in segments)


def canonical_query(parameters: Mapping[str, str]) -> str:
    pairs = sorted(
        (quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in parameters.items()
    )
    return "&".join(f"{key}={value}" for key, value in pairs)


def canonical_request(
    *,
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    signed_headers: str,
    payload_hash: str,
) -> str:
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_headers.split(";"))
    return "\n".join(
        (
            method,
            path,
            query,
            canonical_headers,
            signed_headers,
            payload_hash,
        )
    )


def string_to_sign(*, amz_date: str, credential_scope: str, request: str) -> str:
    hashed = hashlib.sha256(request.encode("utf-8")).hexdigest()
    return "\n".join(("AWS4-HMAC-SHA256", amz_date, credential_scope, hashed))


def amz_timestamp(moment: datetime) -> tuple[str, str]:
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    return stamp, stamp[:8]

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection
from urllib.parse import urlsplit

from django.conf import settings

from apps.files.constants import (
    ATTACHMENT_NAMES,
    GET_TTL_SECONDS,
    MAX_UPLOAD_BYTES,
    POST_TTL_SECONDS,
)
from apps.files.exceptions import (
    ObjectMissing,
    ObjectStatInvalid,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from apps.files.signing import (
    amz_timestamp,
    canonical_object_path,
    canonical_query,
    canonical_request,
    hex_signature,
    host_header,
    signing_key,
    string_to_sign,
)

logger = logging.getLogger(__name__)

_EMPTY_PAYLOAD = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PROFILE_PHOTO_FILENAMES = frozenset(ATTACHMENT_NAMES.values())
_META_SHA = "x-amz-meta-gms-sha256"
_META_TYPE = "x-amz-meta-gms-content-type"
_META_SIZE = "x-amz-meta-gms-byte-size"


@dataclass(frozen=True)
class PresignedPost:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class PresignedGet:
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredObject:
    body: bytes
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class ObjectStat:
    content_length: int
    metadata: Mapping[str, str]


class MinioObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        public_url: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        timeout_seconds: float,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> MinioObjectStore:
        return cls(
            endpoint_url=str(settings.MINIO_ENDPOINT_URL),
            public_url=str(settings.MINIO_PUBLIC_URL),
            bucket=str(settings.MINIO_BUCKET),
            region=str(settings.MINIO_REGION),
            access_key=str(settings.MINIO_ACCESS_KEY),
            secret_key=str(settings.MINIO_SECRET_KEY),
            timeout_seconds=float(settings.MINIO_TIMEOUT_SECONDS),
        )

    def presign_post(
        self,
        *,
        object_key: str,
        content_type: str,
        moment: datetime,
    ) -> PresignedPost:
        self._require_config(public=True)
        expires_at = moment + timedelta(seconds=POST_TTL_SECONDS)
        amz_date, date_stamp = amz_timestamp(moment)
        credential = self._credential(date_stamp)
        # S3 and MinIO require an exact condition for every submitted form field
        # except file, policy, and x-amz-signature. Static credentials omit
        # x-amz-security-token, so that field is neither returned nor conditioned.
        # The bucket condition stays even though the bucket is in the URL.
        policy = {
            "expiration": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conditions": [
                {"bucket": self.bucket},
                ["eq", "$key", object_key],
                ["eq", "$Content-Type", content_type],
                ["content-length-range", 1, MAX_UPLOAD_BYTES],
                ["eq", "$success_action_status", "204"],
                ["eq", "$x-amz-algorithm", "AWS4-HMAC-SHA256"],
                ["eq", "$x-amz-credential", credential],
                ["eq", "$x-amz-date", amz_date],
            ],
        }
        encoded = base64.b64encode(
            json.dumps(policy, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        signature = hex_signature(
            key=signing_key(secret=self.secret_key, date_stamp=date_stamp, region=self.region),
            message=encoded,
        )
        return PresignedPost(
            url=f"{self.public_url}/{self.bucket}",
            expires_at=expires_at,
            fields={
                "key": object_key,
                "Content-Type": content_type,
                "policy": encoded,
                "x-amz-algorithm": "AWS4-HMAC-SHA256",
                "x-amz-credential": credential,
                "x-amz-date": amz_date,
                "x-amz-signature": signature,
                "success_action_status": "204",
            },
        )

    def presign_get(
        self,
        *,
        object_key: str,
        content_type: str,
        filename: str,
        moment: datetime,
    ) -> PresignedGet:
        self._require_config(public=True)
        if filename not in _PROFILE_PHOTO_FILENAMES:
            raise ObjectStoreUnavailable
        expires_at = moment + timedelta(seconds=GET_TTL_SECONDS)
        amz_date, date_stamp = amz_timestamp(moment)
        scope = self._scope(date_stamp)
        credential = f"{self.access_key}/{scope}"
        parameters = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": credential,
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(GET_TTL_SECONDS),
            "X-Amz-SignedHeaders": "host",
            "response-cache-control": "private, no-store",
            "response-content-disposition": f'inline; filename="{filename}"',
            "response-content-type": content_type,
        }
        path = canonical_object_path(self.bucket, object_key)
        query = canonical_query(parameters)
        host = host_header(self.public_url)
        request = canonical_request(
            method="GET",
            path=path,
            query=query,
            headers={"host": host},
            signed_headers="host",
            payload_hash="UNSIGNED-PAYLOAD",
        )
        signature = hex_signature(
            key=signing_key(secret=self.secret_key, date_stamp=date_stamp, region=self.region),
            message=string_to_sign(amz_date=amz_date, credential_scope=scope, request=request),
        )
        return PresignedGet(
            url=f"{self.public_url}{path}?{query}&X-Amz-Signature={signature}",
            expires_at=expires_at,
        )

    def head(self, object_key: str) -> ObjectStat:
        status, headers, _body = self._request("HEAD", object_key, max_bytes=0)
        if status == 404:
            raise ObjectMissing
        if status >= 400:
            raise ObjectStoreUnavailable
        return ObjectStat(
            content_length=_nonnegative_content_length(headers.get("content-length")),
            metadata=headers,
        )

    def read(self, object_key: str) -> StoredObject:
        status, headers, body = self._request("GET", object_key, max_bytes=MAX_UPLOAD_BYTES)
        if status == 404:
            raise ObjectMissing
        if status >= 400:
            raise ObjectStoreUnavailable
        return StoredObject(body=body, metadata=headers)

    def put_canonical(self, object_key: str, body: bytes, *, content_type: str) -> None:
        digest = hashlib.sha256(body).hexdigest()
        headers = {
            "content-type": content_type,
            _META_SHA: digest,
            _META_TYPE: content_type,
            _META_SIZE: str(len(body)),
        }
        status, _response_headers, _response_body = self._request(
            "PUT",
            object_key,
            max_bytes=0,
            body=body,
            extra_headers=headers,
            payload_hash=digest,
        )
        if status >= 400:
            raise ObjectStoreUnavailable

    def delete(self, object_key: str) -> None:
        status, _headers, _body = self._request("DELETE", object_key, max_bytes=0)
        if status in {200, 204, 404}:
            return
        raise ObjectStoreUnavailable

    def _request(
        self,
        method: str,
        object_key: str,
        *,
        max_bytes: int,
        body: bytes = b"",
        extra_headers: Mapping[str, str] | None = None,
        payload_hash: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        self._require_config(public=False)
        parts = urlsplit(self.endpoint_url)
        path = canonical_object_path(self.bucket, object_key)
        host = host_header(self.endpoint_url)
        amz_date, date_stamp = amz_timestamp(_clock())
        content_hash = payload_hash if payload_hash is not None else _EMPTY_PAYLOAD
        headers = {
            "host": host,
            "x-amz-content-sha256": content_hash,
            "x-amz-date": amz_date,
        }
        if extra_headers:
            headers.update(extra_headers)
        signed = ";".join(sorted(headers))
        scope = self._scope(date_stamp)
        request = canonical_request(
            method=method,
            path=path,
            query="",
            headers=headers,
            signed_headers=signed,
            payload_hash=content_hash,
        )
        signature = hex_signature(
            key=signing_key(secret=self.secret_key, date_stamp=date_stamp, region=self.region),
            message=string_to_sign(amz_date=amz_date, credential_scope=scope, request=request),
        )
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        )
        connection_type = HTTPSConnection if parts.scheme == "https" else HTTPConnection
        port = parts.port or (443 if parts.scheme == "https" else 80)
        connection = connection_type(parts.hostname or "", port, timeout=self.timeout_seconds)
        try:
            connection.request(
                method, path, body=body if method != "HEAD" else None, headers=headers
            )
            response = connection.getresponse()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            length = response_headers.get("content-length")
            if (
                method != "HEAD"
                and length is not None
                and length.isdigit()
                and int(length) > max_bytes
            ):
                raise ObjectTooLarge
            payload = b"" if method == "HEAD" else _bounded_read(response, max_bytes)
            return response.status, response_headers, payload
        except (ObjectMissing, ObjectTooLarge, ObjectStoreUnavailable, ObjectStatInvalid):
            raise
        except (TimeoutError, OSError, HTTPException, ValueError):
            logger.warning("object storage request failed", extra={"operation": method})
            raise ObjectStoreUnavailable from None
        finally:
            connection.close()

    def _credential(self, date_stamp: str) -> str:
        return f"{self.access_key}/{self._scope(date_stamp)}"

    def _scope(self, date_stamp: str) -> str:
        return f"{date_stamp}/{self.region}/s3/aws4_request"

    def _require_config(self, *, public: bool) -> None:
        required = (self.bucket, self.region, self.access_key, self.secret_key)
        endpoint = self.public_url if public else self.endpoint_url
        if not endpoint or not all(required):
            logger.warning("object storage is not configured", extra={"operation": "config"})
            raise ObjectStoreUnavailable


def _nonnegative_content_length(value: str | None) -> int:
    """Accept only a decimal byte length. Anything else is an integrity failure."""

    if value is None or len(value) > 20 or not value.isdigit():
        raise ObjectStatInvalid
    return int(value)


def _bounded_read(response: HTTPResponse, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = response.read(min(65_536, remaining))
        if not isinstance(chunk, bytes) or not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise ObjectTooLarge
    return payload


def _clock() -> datetime:
    from datetime import UTC

    from django.utils import timezone

    return timezone.now().astimezone(UTC).replace(microsecond=0)

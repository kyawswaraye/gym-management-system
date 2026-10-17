"""Loopback file-service settings for the browser E2E run.

The staff browser uploads through the public object origin. That origin is
``files.localhost`` when the name resolves only to loopback, because SigV4
signs that Host. When it does not, the signed host is ``127.0.0.1`` so the
signature still matches the Host the browser sends. Server-side MinIO and
ClamAV stay on loopback ports. Root credentials are process environment only
and are never copied into ``NEXT_PUBLIC_*``.
"""

from __future__ import annotations

import argparse
import shlex
import socket
import sys
from collections.abc import Callable, Mapping

LOCAL_PLACEHOLDER_ACCESS_KEY = "local-dev-only"
LOCAL_PLACEHOLDER_SECRET_KEY = "local-dev-only-change"
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})
_FILES_HOST = "files.localhost"


def e2e_reuses_storage_keys() -> bool:
    """E2E seeds delete file rows and never reuse an object key.

    Keys are ``{gym_id}/{random_uuid}``. A later seed may leave the previous
    object in the disposable MinIO volume; it cannot address that object.
    """

    return False


def files_localhost_is_loopback(resolver: Callable[..., object] | None = None) -> bool:
    lookup = resolver or socket.getaddrinfo
    try:
        results = lookup(_FILES_HOST, None)
    except socket.gaierror:
        return False
    if not isinstance(results, list) or not results:
        return False
    addresses: set[str] = set()
    for item in results:
        if not isinstance(item, tuple) or len(item) < 5:
            return False
        sockaddr = item[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            return False
        host = sockaddr[0]
        if not isinstance(host, str):
            return False
        addresses.add(host)
    return addresses <= LOOPBACK_ADDRESSES


def object_hostname(*, files_localhost_resolves: bool) -> str:
    if files_localhost_resolves:
        return _FILES_HOST
    return "127.0.0.1"


def _port(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{name} must be a TCP port.")
    return value


def _credential(value: str, name: str, *, minimum: int) -> str:
    cleaned = value.strip()
    if len(cleaned) < minimum or any(character.isspace() for character in cleaned):
        raise ValueError(f"{name} must be at least {minimum} characters without spaces.")
    return cleaned


def e2e_file_environment(
    *,
    web_port: int,
    proxy_port: int,
    minio_port: int,
    clamav_port: int,
    files_localhost_resolves: bool,
    access_key: str = LOCAL_PLACEHOLDER_ACCESS_KEY,
    secret_key: str = LOCAL_PLACEHOLDER_SECRET_KEY,
) -> dict[str, str]:
    web = _port(web_port, "web port")
    proxy = _port(proxy_port, "object proxy port")
    minio = _port(minio_port, "MinIO port")
    clamav = _port(clamav_port, "ClamAV port")
    access = _credential(access_key, "MinIO access key", minimum=3)
    secret = _credential(secret_key, "MinIO secret key", minimum=8)
    host = object_hostname(files_localhost_resolves=files_localhost_resolves)
    public_url = f"http://{host}:{proxy}"
    return {
        "E2E_FILES_LOCALHOST_RESOLVES": "1" if files_localhost_resolves else "0",
        "E2E_OBJECT_PROXY_PORT": str(proxy),
        "E2E_MINIO_PORT": str(minio),
        "E2E_CLAMAV_PORT": str(clamav),
        "MINIO_ENDPOINT_URL": f"http://127.0.0.1:{minio}",
        "MINIO_PUBLIC_URL": public_url,
        "MINIO_SERVER_URL": public_url,
        "MINIO_BUCKET": "gym-private",
        "MINIO_REGION": "us-east-1",
        "MINIO_ACCESS_KEY": access,
        "MINIO_SECRET_KEY": secret,
        "MINIO_ROOT_USER": access,
        "MINIO_ROOT_PASSWORD": secret,
        "MINIO_TIMEOUT_SECONDS": "10",
        "CLAMAV_HOST": "127.0.0.1",
        "CLAMAV_PORT": str(clamav),
        "CLAMAV_TIMEOUT_SECONDS": "30",
        "OBJECT_PUBLIC_HOST": host,
        "OBJECT_CORS_ORIGIN": f"http://localhost:{web}",
        "OBJECT_PROXY_PORT": str(proxy),
        "NEXT_PUBLIC_OBJECT_ORIGINS": public_url,
        "NEXT_PUBLIC_ALLOW_LOCAL_OBJECT_HTTP": "1",
    }


def shell_exports(env: Mapping[str, str]) -> str:
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in env.items())


def render_e2e_file_shell(
    *,
    web_port: int,
    proxy_port: int,
    minio_port: int,
    clamav_port: int,
    files_localhost_resolves: bool,
    access_key: str = LOCAL_PLACEHOLDER_ACCESS_KEY,
    secret_key: str = LOCAL_PLACEHOLDER_SECRET_KEY,
) -> str:
    env = e2e_file_environment(
        web_port=web_port,
        proxy_port=proxy_port,
        minio_port=minio_port,
        clamav_port=clamav_port,
        files_localhost_resolves=files_localhost_resolves,
        access_key=access_key,
        secret_key=secret_key,
    )
    # Empty adapters select the live MinIO, ClamAV, and Pillow subprocess.
    clears = (
        "unset FILE_OBJECT_STORE",
        "unset FILE_SCANNER",
        "unset FILE_IMAGE_PROCESSOR",
    )
    return "\n".join((shell_exports(env), *clears)) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print E2E file-service shell exports.")
    parser.add_argument("--web-port", type=int, required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--minio-port", type=int, required=True)
    parser.add_argument("--clamav-port", type=int, required=True)
    parser.add_argument("--access-key", default=LOCAL_PLACEHOLDER_ACCESS_KEY)
    parser.add_argument("--secret-key", default=LOCAL_PLACEHOLDER_SECRET_KEY)
    parser.add_argument("--files-localhost-resolves", choices=("0", "1"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    resolves = (
        files_localhost_is_loopback()
        if args.files_localhost_resolves is None
        else args.files_localhost_resolves == "1"
    )
    sys.stdout.write(
        render_e2e_file_shell(
            web_port=args.web_port,
            proxy_port=args.proxy_port,
            minio_port=args.minio_port,
            clamav_port=args.clamav_port,
            files_localhost_resolves=resolves,
            access_key=args.access_key,
            secret_key=args.secret_key,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

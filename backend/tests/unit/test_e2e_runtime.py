import socket

import pytest

from apps.tenants.e2e_runtime import (
    LOCAL_PLACEHOLDER_ACCESS_KEY,
    LOCAL_PLACEHOLDER_SECRET_KEY,
    e2e_file_environment,
    e2e_reuses_storage_keys,
    files_localhost_is_loopback,
    render_e2e_file_shell,
)


def _env(*, resolves: bool) -> dict[str, str]:
    return e2e_file_environment(
        web_port=3010,
        proxy_port=18088,
        minio_port=19000,
        clamav_port=13310,
        files_localhost_resolves=resolves,
    )


def test_e2e_seed_does_not_reuse_object_keys() -> None:
    assert e2e_reuses_storage_keys() is False


def test_files_localhost_requires_every_address_to_be_loopback() -> None:
    def loopback(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    def public(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ]

    def missing(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror("files.localhost was not found")

    assert files_localhost_is_loopback(loopback) is True
    assert files_localhost_is_loopback(public) is False
    assert files_localhost_is_loopback(missing) is False
    assert files_localhost_is_loopback(lambda *_args, **_kwargs: []) is False


def test_signed_host_follows_files_localhost_or_loopback() -> None:
    named = _env(resolves=True)
    assert named["OBJECT_PUBLIC_HOST"] == "files.localhost"
    assert named["MINIO_PUBLIC_URL"] == "http://files.localhost:18088"
    assert named["MINIO_SERVER_URL"] == named["MINIO_PUBLIC_URL"]
    assert named["NEXT_PUBLIC_OBJECT_ORIGINS"] == named["MINIO_PUBLIC_URL"]

    loopback = _env(resolves=False)
    assert loopback["OBJECT_PUBLIC_HOST"] == "127.0.0.1"
    assert loopback["MINIO_PUBLIC_URL"] == "http://127.0.0.1:18088"
    assert loopback["MINIO_SERVER_URL"] == loopback["MINIO_PUBLIC_URL"]
    assert loopback["NEXT_PUBLIC_OBJECT_ORIGINS"] == loopback["MINIO_PUBLIC_URL"]


def test_server_storage_stays_on_loopback_and_off_the_browser() -> None:
    env = _env(resolves=True)
    assert env["MINIO_ENDPOINT_URL"] == "http://127.0.0.1:19000"
    assert env["CLAMAV_HOST"] == "127.0.0.1"
    assert env["CLAMAV_PORT"] == "13310"
    assert env["OBJECT_CORS_ORIGIN"] == "http://localhost:3010"
    assert env["NEXT_PUBLIC_ALLOW_LOCAL_OBJECT_HTTP"] == "1"
    assert env["MINIO_ACCESS_KEY"] == LOCAL_PLACEHOLDER_ACCESS_KEY
    assert env["MINIO_SECRET_KEY"] == LOCAL_PLACEHOLDER_SECRET_KEY
    assert "@" not in env["MINIO_PUBLIC_URL"]
    for key, value in env.items():
        if not key.startswith("NEXT_PUBLIC_"):
            continue
        assert "MINIO" not in key
        assert "SECRET" not in key
        assert LOCAL_PLACEHOLDER_ACCESS_KEY not in value
        assert LOCAL_PLACEHOLDER_SECRET_KEY not in value


def test_shell_renderer_clears_fake_file_adapters() -> None:
    script = render_e2e_file_shell(
        web_port=3010,
        proxy_port=18088,
        minio_port=19000,
        clamav_port=13310,
        files_localhost_resolves=False,
    )
    assert "unset FILE_OBJECT_STORE\n" in script
    assert "unset FILE_SCANNER\n" in script
    assert "unset FILE_IMAGE_PROCESSOR\n" in script
    assert "export MINIO_SECRET_KEY=local-dev-only-change\n" in script
    for line in script.splitlines():
        if line.startswith("export NEXT_PUBLIC_"):
            assert "local-dev-only" not in line


def test_file_environment_rejects_bad_ports_and_secrets() -> None:
    with pytest.raises(ValueError, match="object proxy port"):
        e2e_file_environment(
            web_port=3010,
            proxy_port=0,
            minio_port=19000,
            clamav_port=13310,
            files_localhost_resolves=True,
        )
    with pytest.raises(ValueError, match="secret key"):
        e2e_file_environment(
            web_port=3010,
            proxy_port=18088,
            minio_port=19000,
            clamav_port=13310,
            files_localhost_resolves=True,
            secret_key="short",
        )

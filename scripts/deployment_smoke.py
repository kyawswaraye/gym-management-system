"""Private and public smoke checks for a deployment host.

These checks do not use application credentials. A passing check is not a
restore drill, an SLO, or proof that a production deploy occurred.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import IO

PUBLIC_PROBES = (
    ("login", "/login"),
    ("staff-api", "/api/v1/auth/login/"),
    ("private-health", "/api/v1/health/"),
    ("member-boundary", "/api/v1/member-auth/login/"),
)
# BusyBox wget in prom/prometheus:v3.15.0 is /bin/wget and accepts -T.
# It rejects GNU --timeout. prom/blackbox-exporter:v0.29.0 serves /-/healthy.
# grafana/grafana:13.2.4 provides /usr/bin/curl. clamav/clamav:1.4 provides
# clamdcheck.sh, which Compose uses for the clamav health status.
# These tag checks are not digest proof.
OBSERVABILITY_URLS = (
    ("prometheus", "http://127.0.0.1:9090/-/ready"),
    ("blackbox", "http://blackbox:9115/-/healthy"),
    ("alertmanager", "http://alertmanager:9093/-/ready"),
    ("grafana", "http://grafana:3000/api/health"),
    ("node-exporter", "http://node-exporter:9100/metrics"),
    ("postgres-exporter", "http://postgres-exporter:9187/metrics"),
)
REQUIRED_SERVICES = (
    "caddy",
    "web",
    "api",
    "postgres",
    "clamav",
    "prometheus",
    "alertmanager",
    "grafana",
    "blackbox",
    "node-exporter",
    "postgres-exporter",
)
PRIVATE_SERVICES = ("web", "api", "postgres", "prometheus", "alertmanager", "grafana")
PUBLISHED_PORTS = {80, 443}
MAX_ATTEMPTS = 3
PROBE_TIMEOUT_SECONDS = 5
MAX_BODY_BYTES = 65536
CADDY_NOT_FOUND = b"Not found"
POSTURE_SQL = (
    "SELECT row_to_json(p) FROM ("
    "SELECT current_database() AS database, "
    "(SELECT count(*) FROM django_migrations) AS migration_rows, "
    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = 'gym_runtime') "
    "AS runtime_bypassrls, "
    "(SELECT rolcanlogin FROM pg_roles WHERE rolname = 'gym_runtime') AS runtime_login, "
    "(SELECT rolsuper FROM pg_roles WHERE rolname = 'gym_runtime') AS runtime_super, "
    "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity "
    "AND NOT c.relforcerowsecurity) AS unforced_rls_tables, "
    "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity) "
    "AS forced_rls_tables) p"
)
API_ROLE_SCRIPT = """
import os
import sys
import urllib.request

mode = os.environ.get("DATABASE_ROLE_MODE", "")
user = os.environ.get("RUNTIME_DB_USER", "")
if mode != "runtime" or user != "gym_runtime":
    sys.exit(2)
if "MIGRATION_DB_PASSWORD" in os.environ:
    sys.exit(3)
request = urllib.request.Request("http://127.0.0.1:8000/api/v1/health/", method="GET")
with urllib.request.urlopen(request, timeout=5) as response:
    body = response.read(4096)
    if response.status != 200 or b'"status"' not in body or b"ok" not in body:
        sys.exit(4)
"""
WEB_LOGIN_SCRIPT = (
    "fetch('http://127.0.0.1:3000/login',{redirect:'manual'})"
    ".then((response)=>process.exit(response.status===200?0:1))"
    ".catch(()=>process.exit(1))"
)
EXEC_SERVICES = frozenset({"api", "web", "postgres", "prometheus"})


class SmokeError(RuntimeError):
    """A bounded smoke check failed closed."""


def approve_redirect(original: str, new: str) -> None:
    current = urllib.parse.urlparse(original)
    target = urllib.parse.urlparse(new)
    if current.scheme != "https" or target.scheme != "https":
        raise SmokeError("probe redirect left HTTPS")
    if not current.hostname or target.hostname != current.hostname:
        raise SmokeError("probe redirect left the public host")
    if current.username or current.password or target.username or target.password:
        raise SmokeError("probe URL must not carry user info")


def public_url(host: str, path: str) -> str:
    allowed = {item[1] for item in PUBLIC_PROBES}
    if path not in allowed:
        raise SmokeError("probe path is not allowlisted")
    if "/" in host or "@" in host or host.startswith("."):
        raise SmokeError("public host is invalid")
    return f"https://{host}{path}"


def interpret_public(
    name: str,
    status: int,
    body: bytes,
    original_host: str,
    final_host: str,
) -> None:
    if final_host != original_host:
        raise SmokeError("probe left the public host")
    lowered = body.lower()
    if b"eyj" in lowered or b"bearer " in lowered or b"set-cookie:" in lowered:
        raise SmokeError("probe returned credential material")
    if name == "login":
        if status != 200 or (
            b"<html" not in lowered and b"<!doctype html" not in lowered
        ):
            raise SmokeError("public login probe failed")
        return
    if name in {"staff-api", "private-health"}:
        if status != 404 or body.strip() != CADDY_NOT_FOUND:
            raise SmokeError("blocked API is reachable on the public origin")
        return
    if name == "member-boundary":
        if status == 404 and body.strip() == CADDY_NOT_FOUND:
            raise SmokeError("member boundary is not routed")
        if status not in {401, 403, 405}:
            raise SmokeError("member boundary returned an unexpected status")
        return
    raise SmokeError("unknown public probe")


def interpret_posture(payload: dict[str, object]) -> dict[str, object]:
    database = payload.get("database")
    rows = payload.get("migration_rows")
    bypass = payload.get("runtime_bypassrls")
    login = payload.get("runtime_login")
    superuser = payload.get("runtime_super")
    unforced = payload.get("unforced_rls_tables")
    forced = payload.get("forced_rls_tables")
    if database != "gym_platform":
        raise SmokeError("migration database is not gym_platform")
    if not isinstance(rows, int) or rows < 1:
        raise SmokeError("django migrations are not recorded")
    if bypass is not False or login is not True or superuser is not False:
        raise SmokeError("gym_runtime posture is not the runtime role")
    if unforced != 0 or not isinstance(forced, int) or forced < 1:
        raise SmokeError("forced RLS posture failed")
    return {
        "database": database,
        "migration_rows": rows,
        "forced_rls_tables": forced,
        "unforced_rls_tables": unforced,
        "forced_rls_posture": "pass",
        "restore_proven": False,
    }


def interpret_compose_ps(payload: object) -> None:
    if isinstance(payload, dict):
        rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    elif isinstance(payload, str):
        text = payload.strip()
        if not text:
            raise SmokeError("compose status is empty")
        if text.startswith("["):
            parsed = json.loads(text)
            rows = parsed if isinstance(parsed, list) else [parsed]
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raise SmokeError("compose status is invalid")
    seen: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SmokeError("compose status row is invalid")
        service = row.get("Service") or row.get("Name")
        if not isinstance(service, str):
            raise SmokeError("compose status is missing a service")
        seen[service] = row
        _reject_published_ports(service, row)
    missing = [name for name in REQUIRED_SERVICES if name not in seen]
    if missing:
        raise SmokeError("required service is not running")
    for name in REQUIRED_SERVICES:
        row = seen[name]
        health = row.get("Health")
        state = row.get("State")
        if health in (None, "") and state in (None, ""):
            raise SmokeError("service status is missing")
        if health not in (None, "", "healthy"):
            raise SmokeError("service is not healthy")
        if state not in (None, "", "running"):
            raise SmokeError("service is not running")


def observability_commands(prefix: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if "exec" in prefix:
        raise SmokeError("observability prefix must be the compose command")
    commands = []
    for _name, url in OBSERVABILITY_URLS:
        if not url.startswith("http://") or "://" in url[7:] or "@" in url:
            raise SmokeError("observability probe URL is invalid")
        commands.append(
            (
                *prefix,
                "exec",
                "-T",
                "prometheus",
                "/bin/wget",
                "-q",
                "-O",
                "/dev/null",
                "-T",
                "5",
                url,
            )
        )
    return tuple(commands)


def posture_command(
    prefix: tuple[str, ...], user: str, database: str
) -> tuple[str, ...]:
    if user != "gym_admin" or database != "gym_platform":
        raise SmokeError("posture check identity is not the inventory role")
    if any(marker in POSTURE_SQL.lower() for marker in ("password", "secret", "token")):
        raise SmokeError("posture SQL is unsafe")
    return (
        *prefix,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        user,
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        "-t",
        "-A",
        "-c",
        POSTURE_SQL,
    )


def api_role_command(prefix: tuple[str, ...]) -> tuple[str, ...]:
    if "print(" in API_ROLE_SCRIPT:
        raise SmokeError("api role probe must not print its environment")
    return (*prefix, "exec", "-T", "api", "python", "-c", API_ROLE_SCRIPT)


def web_login_command(prefix: tuple[str, ...]) -> tuple[str, ...]:
    return (*prefix, "exec", "-T", "web", "node", "-e", WEB_LOGIN_SCRIPT)


def run_public_probes(
    host: str,
    fetch: Callable[[str], tuple[int, bytes, str]],
    sleeper: Callable[[float], None],
) -> None:
    for name, path in PUBLIC_PROBES:
        url = public_url(host, path)
        last_error = "probe failed"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, body, final_host = fetch(url)
                interpret_public(name, status, body[:MAX_BODY_BYTES], host, final_host)
                break
            except (SmokeError, TimeoutError, OSError) as exc:
                last_error = str(exc)
                if attempt == MAX_ATTEMPTS:
                    raise SmokeError(last_error) from exc
                sleeper(1)


def fetch_https(
    url: str, timeout: float = PROBE_TIMEOUT_SECONDS
) -> tuple[int, bytes, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SmokeError("public probe must use HTTPS")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "gym-deploy-smoke"},
    )
    if request.has_header("Authorization") or request.has_header("Cookie"):
        raise SmokeError("public probe must not send credentials")
    opener = urllib.request.build_opener(_Redirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES)
            final = urllib.parse.urlparse(response.geturl()).hostname or ""
            return int(response.status), body, final
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_BODY_BYTES)
        final = urllib.parse.urlparse(exc.geturl() or url).hostname or ""
        return int(exc.code), body, final


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        approve_redirect(req.full_url, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _reject_published_ports(service: str, row: dict[str, object]) -> None:
    ports: list[int] = []
    publishers = row.get("Publishers")
    if isinstance(publishers, list):
        for publisher in publishers:
            if isinstance(publisher, dict) and isinstance(
                publisher.get("PublishedPort"), int
            ):
                port = publisher["PublishedPort"]
                if port:
                    ports.append(port)
    for port in ports:
        if service in PRIVATE_SERVICES or service != "caddy":
            raise SmokeError("private service publishes a host port")
        if port not in PUBLISHED_PORTS:
            raise SmokeError("caddy publishes an unexpected host port")

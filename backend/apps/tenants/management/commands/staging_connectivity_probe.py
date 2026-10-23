"""Object-store and ClamAV connectivity for the disposable staging rehearsal.

The probe uses the rehearsal's generated object credentials and the private
ClamAV port. It does not contact Resend, FCM, AWS, or a production host, and
it does not print credentials.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.files.exceptions import ObjectMissing, ObjectStatInvalid, ObjectStoreUnavailable
from apps.files.storage import MinioObjectStore

MARKER_KEY = "staging-rehearsal/marker"
MARKER_BODY = b"local-marker"


def clamav_ping(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zPING\0")
            data = sock.recv(64)
    except OSError:
        return False
    return data.startswith(b"PONG")


def probe_connectivity() -> dict[str, object]:
    if settings.SETTINGS_MODULE != "config.settings.staging_rehearsal":
        raise CommandError("staging connectivity probe requires the rehearsal settings")
    if getattr(settings, "STAGING_REHEARSAL", False) is not True:
        raise CommandError("staging connectivity probe requires the rehearsal flag")
    object_store = "fail"
    try:
        store = MinioObjectStore.from_settings()
        store.put_canonical(MARKER_KEY, MARKER_BODY, content_type="text/plain")
        stat = store.head(MARKER_KEY)
        if stat.content_length == len(MARKER_BODY):
            object_store = "pass"
    except (OSError, ObjectMissing, ObjectStatInvalid, ObjectStoreUnavailable, ValueError):
        object_store = "fail"
    clamav = (
        "pass" if clamav_ping(str(settings.CLAMAV_HOST), int(settings.CLAMAV_PORT), 5) else "fail"
    )
    return {
        "object_store": object_store,
        "clamav": clamav,
        "external_provider_proven": False,
        "production_deploy_proven": False,
    }


class Command(BaseCommand):
    help = "Check the private rehearsal object store and ClamAV. Not a provider proof."

    def handle(self, *args: Any, **options: Any) -> None:
        report = probe_connectivity()
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report["object_store"] != "pass" or report["clamav"] != "pass":
            raise CommandError("staging connectivity probe failed")

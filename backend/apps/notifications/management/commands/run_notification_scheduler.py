from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from types import FrameType
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from apps.notifications.scheduler import (
    HEARTBEAT_PATH,
    NotificationScheduler,
    validate_scheduler_configuration,
    write_heartbeat,
    write_scheduler_metrics,
)
from apps.notifications.scheduler import logger as scheduler_logger


class Command(BaseCommand):
    help = (
        "Run the production notification commands on their documented intervals. "
        "This process serves no HTTP port and accepts no command names."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            del signum, frame
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        metrics_raw = os.environ.get("SCHEDULER_METRICS_PATH", "").strip()
        metrics_path = Path(metrics_raw) if metrics_raw else None
        try:
            validate_scheduler_configuration()
        except ImproperlyConfigured:
            scheduler_logger.info("scheduler_configuration_invalid error_type=ImproperlyConfigured")
            try:
                write_heartbeat(HEARTBEAT_PATH, "fatal")
            except OSError:
                scheduler_logger.info("scheduler_heartbeat_failed error_type=OSError")
            if metrics_path is not None:
                try:
                    write_scheduler_metrics(metrics_path, "fatal")
                except OSError:
                    scheduler_logger.info("scheduler_metrics_failed error_type=OSError")
            stop.wait()
            scheduler_logger.info("scheduler_stopped")
            return
        NotificationScheduler(stop=stop, metrics_path=metrics_path).serve()

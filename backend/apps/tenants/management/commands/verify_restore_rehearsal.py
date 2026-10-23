import json
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.common.restore_verification import (
    RestoreCursor,
    collect_restore_report,
    judge_migrations,
)


class Command(BaseCommand):
    help = (
        "Read-only verification of a disposable restored database. "
        "This does not prove RPO or RTO and does not download object bytes."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        with transaction.atomic():
            with connection.cursor() as cursor:
                report = collect_restore_report(cast(RestoreCursor, cursor))
            loader = MigrationLoader(connection, ignore_no_migrations=False)
            automated = report["automated"]
            if not isinstance(automated, dict):
                raise CommandError("restore verification report is invalid")
            automated["migrations"] = judge_migrations(
                set(loader.graph.nodes), set(loader.applied_migrations)
            )
            report["ok"] = all(value == "pass" for value in automated.values())
        self.stdout.write(json.dumps(report, sort_keys=True, default=str))
        if report["ok"] is not True:
            raise CommandError("restore verification failed")

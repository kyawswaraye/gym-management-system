from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.users.services import purge_terminal_gym_selection_tickets


class Command(BaseCommand):
    help = "Purge terminal gym-selection tickets after the mandatory retention window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = int(options["batch_size"])
        dry_run = bool(options["dry_run"])
        try:
            if dry_run:
                eligible = purge_terminal_gym_selection_tickets(
                    batch_size=batch_size,
                    dry_run=True,
                )
                self.stdout.write(f"{eligible} terminal ticket(s) eligible for purge.")
                return

            total = 0
            while True:
                deleted = purge_terminal_gym_selection_tickets(batch_size=batch_size)
                total += deleted
                if deleted < batch_size:
                    break
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except DatabaseError as exc:
            raise CommandError(
                "Ticket purge requires a privileged maintenance database identity."
            ) from exc
        self.stdout.write(self.style.SUCCESS(f"Purged {total} terminal ticket(s)."))

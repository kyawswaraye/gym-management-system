from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.tenants.services import provision_gym


class Command(BaseCommand):
    help = "Idempotently provision a gym, initial owner, roles, and feature defaults."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--code", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--owner-email", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = provision_gym(
                code=options["code"],
                name=options["name"],
                owner_email=options["owner_email"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Gym {result.gym.code} provisioned with owner membership "
                f"{result.owner_membership.id}."
            )
        )

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.members.services import gym_today
from apps.notifications.services import (
    SCHEDULE_BATCH_MAX,
    schedule_membership_expiry_notifications,
)
from apps.tenants.models import Gym, GymSettings


class Command(BaseCommand):
    help = "Create deduplicated membership-expiry email notifications."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--gym-code")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > SCHEDULE_BATCH_MAX:
            raise CommandError(f"batch-size must be between 1 and {SCHEDULE_BATCH_MAX}")

        gyms = Gym.objects.filter(status=Gym.Status.ACTIVE).order_by("id")
        gym_code = options.get("gym_code")
        if gym_code:
            gyms = gyms.filter(code=gym_code)
            if not gyms.exists():
                raise CommandError(f"No active gym matches code {gym_code}.")
        gym_rows = list(gyms.values_list("id", "code"))

        total = 0
        errors: list[str] = []
        for gym_id, gym_code in gym_rows:
            try:
                with transaction.atomic():
                    set_gym_context(gym_id=gym_id)
                    try:
                        gym = Gym.objects.get(pk=gym_id)
                        total += schedule_membership_expiry_notifications(
                            gym=gym,
                            today=gym_today(gym),
                            batch_size=batch_size,
                        )
                    finally:
                        clear_gym_context()
            except (ValueError, GymSettings.DoesNotExist) as exc:
                message = f"Gym {gym_code}: {exc}"
                errors.append(message)
                self.stderr.write(message)

        if errors:
            raise CommandError(
                f"Scheduling failed for {len(errors)} gym(s); other gyms were processed."
            )
        self.stdout.write(self.style.SUCCESS(f"Scheduled {total} membership-expiry email(s)."))

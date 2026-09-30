from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today
from apps.notifications.services import schedule_membership_expiry_notifications
from apps.tenants.models import Gym, GymSettings


class Command(BaseCommand):
    help = "Create deduplicated membership-expiry email notifications."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--gym-code")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > 5000:
            raise CommandError("batch-size must be between 1 and 5000")

        gyms = Gym.objects.filter(status=Gym.Status.ACTIVE).order_by("id")
        gym_code = options.get("gym_code")
        if gym_code:
            gyms = gyms.filter(code=gym_code)

        total = 0
        errors: list[str] = []
        for gym in gyms.iterator(chunk_size=100):
            try:
                with transaction.atomic():
                    set_gym_context(gym_id=gym.id)
                    total += schedule_membership_expiry_notifications(
                        gym=gym,
                        today=gym_today(gym),
                        batch_size=batch_size,
                    )
            except (ValueError, GymSettings.DoesNotExist) as exc:
                message = f"Gym {gym.code}: {exc}"
                errors.append(message)
                self.stderr.write(message)

        if errors:
            raise CommandError(
                f"Scheduling failed for {len(errors)} gym(s); other gyms were processed."
            )
        self.stdout.write(self.style.SUCCESS(f"Scheduled {total} membership-expiry email(s)."))

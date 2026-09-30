from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today, refresh_due_memberships
from apps.tenants.models import Gym


class Command(BaseCommand):
    help = "Activate or expire memberships using each gym's local calendar date."

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

        activated_total = 0
        expired_total = 0
        for gym in gyms.iterator(chunk_size=100):
            while True:
                with transaction.atomic():
                    set_gym_context(gym_id=gym.id)
                    activated, expired = refresh_due_memberships(
                        gym=gym,
                        today=gym_today(gym),
                        batch_size=batch_size,
                    )
                activated_total += activated
                expired_total += expired
                if activated + expired < batch_size:
                    break

        self.stdout.write(
            self.style.SUCCESS(
                f"Activated {activated_total} and expired {expired_total} membership(s)."
            )
        )

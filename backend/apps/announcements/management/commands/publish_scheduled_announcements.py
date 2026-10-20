from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.announcements.services import publish_due_announcement
from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.tenants.models import Gym

_BATCH_MAX = 500


class Command(BaseCommand):
    help = "Publish due scheduled announcements and insert recipient rows."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--gym-code")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > _BATCH_MAX:
            raise CommandError(f"batch-size must be between 1 and {_BATCH_MAX}")
        gyms = Gym.objects.filter(status=Gym.Status.ACTIVE).order_by("id")
        gym_code = options.get("gym_code")
        if gym_code:
            gyms = gyms.filter(code=gym_code)
            if not gyms.exists():
                raise CommandError(f"No active gym matches code {gym_code}.")
        published = 0
        now = timezone.now()
        for gym_id, _code in list(gyms.values_list("id", "code")):
            for _ in range(batch_size):
                with transaction.atomic():
                    set_gym_context(gym_id=gym_id)
                    try:
                        gym = Gym.objects.get(pk=gym_id)
                        changed = publish_due_announcement(gym=gym, now=now)
                    finally:
                        clear_gym_context()
                if not changed:
                    break
                published += 1
        self.stdout.write(self.style.SUCCESS(f"Published {published} scheduled announcement(s)."))

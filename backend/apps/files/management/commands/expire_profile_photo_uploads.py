from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.files.runtime import object_store
from apps.files.services import expire_quarantined_uploads
from apps.tenants.models import Gym


class Command(BaseCommand):
    help = (
        "Reject abandoned profile-photo uploads, retire unattached clean photos, "
        "and retry private object removal."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--gym-code")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = int(options["limit"])
        if limit < 1 or limit > 500:
            raise CommandError("limit must be between 1 and 500")
        gyms = Gym.objects.all().order_by("id")
        gym_code = options.get("gym_code")
        if gym_code:
            gyms = gyms.filter(code=gym_code)
            if not gyms.exists():
                raise CommandError(f"No gym matches code {gym_code}.")
        store = object_store()
        total = 0
        for gym in gyms:
            total += expire_quarantined_uploads(gym=gym, store=store, limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Processed {total} profile photo row(s)."))

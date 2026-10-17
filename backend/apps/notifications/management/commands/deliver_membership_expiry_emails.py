from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.members.services import gym_today
from apps.notifications.models import MAX_NOTIFICATION_ATTEMPTS
from apps.notifications.services import (
    DELIVERY_BATCH_DEFAULT,
    DELIVERY_BATCH_MAX,
    DeliveryOutcome,
    deliver_next_membership_expiry_email,
)
from apps.tenants.models import Gym, GymSettings


class Command(BaseCommand):
    help = (
        "Deliver due membership-expiry emails through Django's configured email backend. "
        "Each row is locked and committed separately. Concurrent runs skip locked rows. "
        "A crash after the provider accepts a message and before commit can still duplicate "
        "that send unless the provider honors X-Idempotency-Key."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--batch-size", type=int, default=DELIVERY_BATCH_DEFAULT)
        parser.add_argument("--max-attempts", type=int, default=MAX_NOTIFICATION_ATTEMPTS)
        parser.add_argument("--gym-code")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = int(options["batch_size"])
        max_attempts = int(options["max_attempts"])
        if batch_size < 1 or batch_size > DELIVERY_BATCH_MAX:
            raise CommandError(f"batch-size must be between 1 and {DELIVERY_BATCH_MAX}")
        if not 1 <= max_attempts <= MAX_NOTIFICATION_ATTEMPTS:
            raise CommandError(f"max-attempts must be between 1 and {MAX_NOTIFICATION_ATTEMPTS}")

        gyms = Gym.objects.filter(status=Gym.Status.ACTIVE).order_by("id")
        gym_code = options.get("gym_code")
        if gym_code:
            gyms = gyms.filter(code=gym_code)
            if not gyms.exists():
                raise CommandError(f"No active gym matches code {gym_code}.")
        gym_rows = list(gyms.values_list("id", "code"))

        sent = 0
        cancelled = 0
        retried = 0
        failed = 0
        errors: list[str] = []
        for gym_id, gym_code in gym_rows:
            try:
                for _ in range(batch_size):
                    with transaction.atomic():
                        set_gym_context(gym_id=gym_id)
                        try:
                            gym = Gym.objects.get(pk=gym_id)
                            outcome = deliver_next_membership_expiry_email(
                                gym=gym,
                                today=gym_today(gym),
                                max_attempts=max_attempts,
                            )
                        finally:
                            clear_gym_context()
                    if outcome is None:
                        break
                    if outcome is DeliveryOutcome.SENT:
                        sent += 1
                    elif outcome is DeliveryOutcome.CANCELLED:
                        cancelled += 1
                    elif outcome is DeliveryOutcome.RETRIED:
                        retried += 1
                    elif outcome is DeliveryOutcome.FAILED:
                        failed += 1
            except (ValueError, GymSettings.DoesNotExist) as exc:
                message = f"Gym {gym_code}: {exc}"
                errors.append(message)
                self.stderr.write(message)

        if errors:
            raise CommandError(
                f"Delivery failed for {len(errors)} gym(s); other gyms were processed."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Delivered "
                f"{sent}, cancelled {cancelled}, retried {retried}, and failed {failed} "
                "membership-expiry email(s)."
            )
        )

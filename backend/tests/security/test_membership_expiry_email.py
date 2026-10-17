from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan
from apps.members.services import cancel_membership, create_membership, gym_today
from apps.notifications.models import MAX_NOTIFICATION_ATTEMPTS, Notification
from apps.notifications.services import (
    DELIVERY_BATCH_MAX,
    DELIVERY_REJECTED,
    IDEMPOTENCY_HEADER,
    PROVIDER_UNAVAILABLE,
    SCHEDULE_BATCH_MAX,
    DeliveryOutcome,
    deliver_next_membership_expiry_email,
    schedule_membership_expiry_notifications,
)
from apps.tenants.models import Gym, GymFeatureEntitlement, GymSettings
from apps.tenants.services import provision_gym
from apps.users.models import User
from tests.mail_backends import PROVIDER_SECRET, SlowBackend

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"


def _provision(code: str):
    owner = User.objects.create_user(f"{code.lower()}@example.test", PASSWORD)
    with transaction.atomic():
        return provision_gym(code=code, name=f"{code} Gym", owner_email=owner.email)


def _plan(tenant, *, code: str = "YEAR") -> MembershipPlan:
    plan, _ = MembershipPlan.objects.get_or_create(
        gym=tenant.gym,
        code=code,
        defaults={
            "name": "Snapshot Year",
            "duration_value": 365,
            "duration_unit": MembershipPlan.DurationUnit.DAY,
            "price": Decimal("1000.00"),
            "currency": tenant.gym.currency,
            "created_by_membership_id": tenant.owner_membership.id,
            "updated_by_membership_id": tenant.owner_membership.id,
        },
    )
    return plan


def _member(tenant, *, number: str, first_name: str, email: str | None, today) -> Member:
    return Member.objects.create(
        gym=tenant.gym,
        member_number=number,
        first_name=first_name,
        email=email,
        join_date=today,
        created_by_membership_id=tenant.owner_membership.id,
        updated_by_membership_id=tenant.owner_membership.id,
    )


def _sell(tenant, *, member: Member, plan: MembershipPlan, warning_day: int, today) -> Membership:
    start = today + timedelta(days=warning_day) - timedelta(days=364)
    return create_membership(
        gym=tenant.gym,
        actor=tenant.owner_membership,
        member=member,
        plan=plan,
        start_date=start,
        discount_amount=Decimal("0"),
    )


def _prepare(tenant, *, warning_days: list[int] | None = None, email_enabled: bool = True) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        settings_row = GymSettings.objects.get(gym=tenant.gym)
        settings_row.membership_expiry_email_enabled = email_enabled
        if warning_days is not None:
            settings_row.membership_expiry_warning_days = warning_days
        settings_row.save(
            update_fields=[
                "membership_expiry_email_enabled",
                "membership_expiry_warning_days",
                "updated_at",
            ]
        )


def _schedule(tenant, *, batch_size: int = 50) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        return schedule_membership_expiry_notifications(
            gym=tenant.gym,
            today=gym_today(tenant.gym),
            batch_size=batch_size,
        )


def _deliver(tenant, *, now=None, max_attempts: int = MAX_NOTIFICATION_ATTEMPTS):
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        return deliver_next_membership_expiry_email(
            gym=tenant.gym,
            today=gym_today(tenant.gym),
            max_attempts=max_attempts,
            now=now,
        )


def _stored_text(notification: Notification) -> str:
    return json.dumps(
        {
            "payload": notification.payload,
            "last_error_code": notification.last_error_code,
            "provider_id": notification.provider_id,
            "deduplication_key": notification.deduplication_key,
        }
    )


def test_warning_day_boundaries_skip_missing_email_and_repeat_runs() -> None:
    tenant = _provision("EXPIRY-DAYS")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[0, 3, 7, 30])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        for day in (0, 1, 3, 7, 30, 31):
            member = _member(
                tenant,
                number=f"DAY-{day:02d}",
                first_name=f"Day{day}",
                email=f"day{day}@example.test",
                today=today,
            )
            _sell(tenant, member=member, plan=plan, warning_day=day, today=today)
        silent = _member(
            tenant,
            number="DAY-NONE",
            first_name="Silent",
            email=None,
            today=today,
        )
        _sell(tenant, member=silent, plan=plan, warning_day=7, today=today)

    assert _schedule(tenant) == 4
    assert _schedule(tenant) == 0
    notifications = Notification.objects.filter(gym=tenant.gym)
    assert notifications.count() == 4
    assert set(notifications.values_list("channel", flat=True)) == {Notification.Channel.EMAIL}
    assert not notifications.filter(recipient_member__email__isnull=True).exists()
    assert set(notifications.values_list("payload__warning_day", flat=True)) == {0, 3, 7, 30}
    for notification in notifications:
        assert "email" not in notification.payload
        assert notification.recipient_member.email not in _stored_text(notification)
        assert notification.next_attempt_at == notification.scheduled_at
        assert notification.last_error_code == ""


def test_disabled_setting_and_entitlement_schedule_nothing() -> None:
    tenant = _provision("EXPIRY-OFF")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7], email_enabled=False)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant, number="OFF-1", first_name="Off", email="off@example.test", today=today
        )
        _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 0
    assert not Notification.objects.filter(gym=tenant.gym).exists()

    _prepare(tenant, warning_days=[7], email_enabled=True)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        GymFeatureEntitlement.objects.filter(gym=tenant.gym, feature__code="memberships").update(
            enabled=False
        )
    assert _schedule(tenant) == 0
    assert not Notification.objects.filter(gym=tenant.gym).exists()


def test_inactive_membership_and_member_are_skipped_or_cancelled() -> None:
    tenant = _provision("EXPIRY-STATE")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        suspended_member = _member(
            tenant,
            number="STATE-S",
            first_name="Suspended",
            email="suspended-member@example.test",
            today=today,
        )
        suspended_membership = _sell(
            tenant, member=suspended_member, plan=plan, warning_day=7, today=today
        )
        archived = _member(
            tenant,
            number="STATE-A",
            first_name="Archived",
            email="archived@example.test",
            today=today,
        )
        _sell(tenant, member=archived, plan=plan, warning_day=7, today=today)
        cancelled_member = _member(
            tenant,
            number="STATE-C",
            first_name="Cancelled",
            email="cancelled-member@example.test",
            today=today,
        )
        _sell(tenant, member=cancelled_member, plan=plan, warning_day=7, today=today)
        Membership.objects.filter(pk=suspended_membership.pk).update(
            status=Membership.Status.SUSPENDED
        )
        archived.status = Member.Status.ARCHIVED
        archived.archived_at = timezone.now()
        archived.save(update_fields=["status", "archived_at", "updated_at"])
        cancelled_member.status = Member.Status.CANCELLED
        cancelled_member.save(update_fields=["status", "updated_at"])

    assert _schedule(tenant) == 0

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        active = _member(
            tenant, number="STATE-OK", first_name="Ready", email="ready@example.test", today=today
        )
        membership = _sell(tenant, member=active, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        Membership.objects.filter(pk=membership.pk).update(status=Membership.Status.EXPIRED)
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    notification = Notification.objects.get(gym=tenant.gym)
    assert notification.status == Notification.Status.CANCELLED
    assert notification.attempt_count == 0
    assert notification.sent_at is None
    assert notification.failed_at is None


def test_removed_changed_and_injected_email_use_current_member_only() -> None:
    tenant = _provision("EXPIRY-ADDR")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[3])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        removed = _member(
            tenant,
            number="ADDR-R",
            first_name="Removed",
            email="removed@example.test",
            today=today,
        )
        changed = _member(
            tenant,
            number="ADDR-C",
            first_name="Changed",
            email="before@example.test",
            today=today,
        )
        injected = _member(
            tenant,
            number="ADDR-I",
            first_name="Plain",
            email="plain@example.test",
            today=today,
        )
        _sell(tenant, member=removed, plan=plan, warning_day=3, today=today)
        _sell(tenant, member=changed, plan=plan, warning_day=3, today=today)
        _sell(tenant, member=injected, plan=plan, warning_day=3, today=today)
    assert _schedule(tenant) == 3
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        removed.email = None
        removed.save(update_fields=["email", "updated_at"])
        changed.email = "after@example.test"
        changed.first_name = "After"
        changed.save(
            update_fields=[
                "email",
                "first_name",
                "last_name",
                "normalized_full_name",
                "updated_at",
            ]
        )
        Member.objects.filter(pk=injected.pk).update(first_name="Ann\nBcc: evil@example.test")
        tenant.gym.email = "desk@example.test"
        tenant.gym.phone = "15550001111"
        tenant.gym.save(update_fields=["email", "phone", "updated_at"])
        plan.name = "Renamed Catalog"
        plan.save(update_fields=["name", "updated_at"])

    from django.core import mail

    mail.outbox.clear()
    outcomes = [_deliver(tenant) for _ in range(3)]
    assert outcomes.count(DeliveryOutcome.CANCELLED) == 1
    assert outcomes.count(DeliveryOutcome.SENT) == 2
    removed_row = Notification.objects.get(recipient_member=removed)
    assert removed_row.status == Notification.Status.CANCELLED
    assert removed_row.attempt_count == 0
    sent = [message for message in mail.outbox]
    assert sorted(address for message in sent for address in message.to) == [
        "after@example.test",
        "plain@example.test",
    ]
    changed_message = next(message for message in sent if message.to == ["after@example.test"])
    assert "before@example.test" not in changed_message.body
    assert "Hello After," in changed_message.body
    assert "Snapshot Year" in changed_message.body
    assert "Renamed Catalog" not in changed_message.body
    assert "desk@example.test" in changed_message.body
    assert "15550001111" in changed_message.body
    assert "after@example.test" not in changed_message.body
    changed_row = Notification.objects.get(recipient_member=changed)
    assert changed_message.extra_headers[IDEMPOTENCY_HEADER] == changed_row.deduplication_key
    assert "\n" not in changed_message.subject
    injected_message = next(message for message in sent if message.to == ["plain@example.test"])
    assert "\n" not in injected_message.subject
    assert "\r" not in injected_message.subject
    assert set(injected_message.extra_headers) == {IDEMPOTENCY_HEADER}


def test_send_time_setting_entitlement_renewal_and_suspended_gym_do_not_send() -> None:
    from django.core import mail

    tenant = _provision("EXPIRY-RENEW")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        setting_member = _member(
            tenant,
            number="RENEW-S",
            first_name="Setting",
            email="setting@example.test",
            today=today,
        )
        entitlement_member = _member(
            tenant,
            number="RENEW-E",
            first_name="Entitlement",
            email="entitlement@example.test",
            today=today,
        )
        _sell(tenant, member=setting_member, plan=plan, warning_day=7, today=today)
        _sell(tenant, member=entitlement_member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 2
    _prepare(tenant, warning_days=[7], email_enabled=False)
    mail.outbox.clear()
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    assert mail.outbox == []

    _prepare(tenant, warning_days=[7], email_enabled=True)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        queued = _member(
            tenant,
            number="RENEW-Q",
            first_name="Queued",
            email="queued@example.test",
            today=today,
        )
        _sell(tenant, member=queued, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        GymFeatureEntitlement.objects.filter(gym=tenant.gym, feature__code="memberships").update(
            enabled=False
        )
    mail.outbox.clear()
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    assert mail.outbox == []

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        GymFeatureEntitlement.objects.filter(gym=tenant.gym, feature__code="memberships").update(
            enabled=True
        )
        renewing = _member(
            tenant, number="RENEW-1", first_name="Renew", email="renew@example.test", today=today
        )
        original = _sell(tenant, member=renewing, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        cancel_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            membership_id=original.id,
            reason="Renewed",
        )
        replacement = _sell(tenant, member=renewing, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    mail.outbox.clear()
    outcomes = [_deliver(tenant) for _ in range(2)]
    assert sorted(outcome.value if outcome else "" for outcome in outcomes) == ["CANCELLED", "SENT"]
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["renew@example.test"]
    assert Notification.objects.get(membership=original).status == Notification.Status.CANCELLED
    assert Notification.objects.get(membership=replacement).status == Notification.Status.SENT
    assert _deliver(tenant) is None
    assert len(mail.outbox) == 1

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        suspended = _member(
            tenant,
            number="RENEW-G",
            first_name="Gym",
            email="gym-status@example.test",
            today=today,
        )
        _sell(tenant, member=suspended, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    tenant.gym.status = Gym.Status.SUSPENDED
    tenant.gym.save(update_fields=["status", "updated_at"])
    mail.outbox.clear()
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    assert mail.outbox == []
    assert _schedule(tenant) == 0


def test_malformed_payload_is_cancelled_and_destination_keys_are_rejected() -> None:
    tenant = _provision("EXPIRY-BAD")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[0])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant, number="BAD-1", first_name="Bad", email="bad@example.test", today=today
        )
        _sell(tenant, member=member, plan=plan, warning_day=0, today=today)
    assert _schedule(tenant) == 1
    notification = Notification.objects.get(gym=tenant.gym)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=tenant.gym.id)
            notification.payload = {**notification.payload, "email": "hidden@example.test"}
            notification.save(update_fields=["payload", "updated_at"])
    notification.refresh_from_db()
    assert "email" not in notification.payload
    notification.payload = {**notification.payload, "plan_name": "Tampered Plan"}
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        notification.save(update_fields=["payload", "updated_at"])
    from django.core import mail

    mail.outbox.clear()
    assert _deliver(tenant) is DeliveryOutcome.CANCELLED
    assert mail.outbox == []
    notification.refresh_from_db()
    assert notification.status == Notification.Status.CANCELLED
    assert notification.attempt_count == 0


def test_provider_success_failure_zero_send_and_bounded_retry() -> None:
    from django.core import mail

    tenant = _provision("EXPIRY-SEND")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant, number="SEND-1", first_name="Send", email="send@example.test", today=today
        )
        _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    mail.outbox.clear()
    assert _deliver(tenant) is DeliveryOutcome.SENT
    notification = Notification.objects.get(gym=tenant.gym)
    assert notification.status == Notification.Status.SENT
    assert notification.attempt_count == 1
    assert notification.sent_at is not None
    assert notification.failed_at is None
    assert notification.next_attempt_at is None
    assert notification.last_error_code == ""
    assert len(mail.outbox) == 1
    assert _deliver(tenant) is None
    assert len(mail.outbox) == 1

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        notification.status = Notification.Status.PENDING
        notification.sent_at = None
        notification.attempt_count = 0
        notification.next_attempt_at = timezone.now()
        notification.save(
            update_fields=[
                "status",
                "sent_at",
                "attempt_count",
                "next_attempt_at",
                "updated_at",
            ]
        )

    with override_settings(EMAIL_BACKEND="tests.mail_backends.ProviderOutageBackend"):
        assert _deliver(tenant) is DeliveryOutcome.RETRIED
    notification.refresh_from_db()
    assert notification.status == Notification.Status.PENDING
    assert notification.attempt_count == 1
    assert notification.last_error_code == PROVIDER_UNAVAILABLE
    assert notification.failed_at is None
    assert notification.next_attempt_at is not None
    assert PROVIDER_SECRET not in _stored_text(notification)
    assert "send@example.test" not in notification.last_error_code
    immediate = notification.next_attempt_at - timedelta(seconds=1)
    assert _deliver(tenant, now=immediate) is None

    with override_settings(EMAIL_BACKEND="tests.mail_backends.ZeroSendBackend"):
        moment = notification.next_attempt_at
        assert _deliver(tenant, now=moment) is DeliveryOutcome.RETRIED
    notification.refresh_from_db()
    assert notification.attempt_count == 2
    assert notification.last_error_code == DELIVERY_REJECTED
    delay = notification.next_attempt_at - moment
    assert delay == timedelta(seconds=600)

    with override_settings(EMAIL_BACKEND="tests.mail_backends.ProviderOutageBackend"):
        for attempt in (3, 4):
            moment = notification.next_attempt_at
            assert _deliver(tenant, now=moment) is DeliveryOutcome.RETRIED
            notification.refresh_from_db()
            assert notification.attempt_count == attempt
            assert notification.status == Notification.Status.PENDING
        moment = notification.next_attempt_at
        assert _deliver(tenant, now=moment) is DeliveryOutcome.FAILED
    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.attempt_count == MAX_NOTIFICATION_ATTEMPTS
    assert notification.failed_at == moment
    assert notification.next_attempt_at is None
    assert notification.last_error_code == PROVIDER_UNAVAILABLE
    assert PROVIDER_SECRET not in _stored_text(notification)
    assert _deliver(tenant, now=moment + timedelta(days=1)) is None
    assert len(mail.outbox) == 1


def test_commands_filter_gyms_bound_batches_and_clear_tenant_context() -> None:
    from django.core import mail

    first = _provision("EXPIRY-CMD-A")
    second = _provision("EXPIRY-CMD-B")
    for tenant, address in ((first, "a@example.test"), (second, "b@example.test")):
        today = gym_today(tenant.gym)
        _prepare(tenant, warning_days=[7])
        with transaction.atomic():
            set_gym_context(gym_id=tenant.gym.id)
            plan = _plan(tenant)
            for index in range(3):
                member = _member(
                    tenant,
                    number=f"CMD-{index}",
                    first_name=f"Cmd{index}",
                    email=f"{index}-{address}",
                    today=today,
                )
                _sell(tenant, member=member, plan=plan, warning_day=7, today=today)

    output = StringIO()
    call_command(
        "schedule_membership_expiry_emails",
        batch_size=2,
        gym_code="EXPIRY-CMD-A",
        stdout=output,
    )
    assert "Scheduled 2" in output.getvalue()
    assert Notification.objects.filter(gym=first.gym).count() == 2
    assert Notification.objects.filter(gym=second.gym).count() == 0
    again = StringIO()
    call_command("schedule_membership_expiry_emails", gym_code="EXPIRY-CMD-A", stdout=again)
    assert "Scheduled 1" in again.getvalue()
    assert Notification.objects.filter(gym=first.gym).count() == 3
    call_command("schedule_membership_expiry_emails", gym_code="EXPIRY-CMD-B")
    assert Notification.objects.filter(gym=second.gym).count() == 3

    with pytest.raises(CommandError, match="batch-size"):
        call_command("schedule_membership_expiry_emails", batch_size=0)
    with pytest.raises(CommandError, match="batch-size"):
        call_command("schedule_membership_expiry_emails", batch_size=SCHEDULE_BATCH_MAX + 1)
    with pytest.raises(CommandError, match="No active gym"):
        call_command("deliver_membership_expiry_emails", gym_code="MISSING-GYM")
    with pytest.raises(CommandError, match="batch-size"):
        call_command("deliver_membership_expiry_emails", batch_size=DELIVERY_BATCH_MAX + 1)
    with pytest.raises(CommandError, match="max-attempts"):
        call_command("deliver_membership_expiry_emails", max_attempts=0)
    with pytest.raises(CommandError, match="max-attempts"):
        call_command(
            "deliver_membership_expiry_emails",
            max_attempts=MAX_NOTIFICATION_ATTEMPTS + 1,
        )

    mail.outbox.clear()
    delivered = StringIO()
    call_command(
        "deliver_membership_expiry_emails",
        batch_size=2,
        gym_code="EXPIRY-CMD-A",
        stdout=delivered,
    )
    assert "Delivered 2" in delivered.getvalue()
    assert len(mail.outbox) == 2
    assert {message.to[0] for message in mail.outbox} <= {
        "0-a@example.test",
        "1-a@example.test",
        "2-a@example.test",
    }
    pending = Notification.objects.filter(gym=second.gym, status=Notification.Status.PENDING)
    assert pending.count() == 3
    call_command("deliver_membership_expiry_emails", gym_code="EXPIRY-CMD-A")
    assert len(mail.outbox) == 3
    repeat = StringIO()
    call_command("deliver_membership_expiry_emails", gym_code="EXPIRY-CMD-A", stdout=repeat)
    assert "Delivered 0" in repeat.getvalue()
    assert len(mail.outbox) == 3

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.gym_id', true)")
        assert cursor.fetchone()[0] in (None, "")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT count(*) FROM notifications_notification")
            assert cursor.fetchone()[0] == 0
    with transaction.atomic():
        set_gym_context(gym_id=first.gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        visible = set(Notification.objects.values_list("gym_id", flat=True))
        assert visible == {first.gym.id}

    first.gym.status = Gym.Status.SUSPENDED
    first.gym.save(update_fields=["status", "updated_at"])
    with pytest.raises(CommandError, match="No active gym"):
        call_command("schedule_membership_expiry_emails", gym_code="EXPIRY-CMD-A")


def test_lower_max_attempts_fails_due_row_without_another_send() -> None:
    from django.core import mail

    tenant = _provision("EXPIRY-CAP")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant, number="CAP-1", first_name="Cap", email="cap@example.test", today=today
        )
        _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    notification = Notification.objects.get(gym=tenant.gym)
    moment = timezone.now()
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        notification.attempt_count = 4
        notification.last_error_code = PROVIDER_UNAVAILABLE
        notification.next_attempt_at = moment
        notification.save(
            update_fields=[
                "attempt_count",
                "last_error_code",
                "next_attempt_at",
                "updated_at",
            ]
        )
    mail.outbox.clear()
    assert _deliver(tenant, max_attempts=3, now=moment) is DeliveryOutcome.FAILED
    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.attempt_count == 4
    assert notification.last_error_code == PROVIDER_UNAVAILABLE
    assert notification.failed_at == moment
    assert notification.sent_at is None
    assert notification.next_attempt_at is None
    assert mail.outbox == []
    assert "cap@example.test" not in _stored_text(notification)

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        notification.status = Notification.Status.PENDING
        notification.attempt_count = 4
        notification.failed_at = None
        notification.last_error_code = ""
        notification.next_attempt_at = moment
        notification.save(
            update_fields=[
                "status",
                "attempt_count",
                "failed_at",
                "last_error_code",
                "next_attempt_at",
                "updated_at",
            ]
        )
    assert _deliver(tenant, max_attempts=3, now=moment) is DeliveryOutcome.FAILED
    notification.refresh_from_db()
    assert notification.attempt_count == 4
    assert notification.last_error_code == DELIVERY_REJECTED
    assert notification.next_attempt_at is None
    assert mail.outbox == []


def test_command_finalizes_attempt_already_at_configured_max() -> None:
    from django.core import mail

    tenant = _provision("EXPIRY-CAP-CMD")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant,
            number="CAP-CMD",
            first_name="CapCmd",
            email="cap-cmd@example.test",
            today=today,
        )
        _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    notification = Notification.objects.get(gym=tenant.gym)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        notification.attempt_count = 4
        notification.last_error_code = PROVIDER_UNAVAILABLE
        notification.next_attempt_at = timezone.now()
        notification.save(
            update_fields=[
                "attempt_count",
                "last_error_code",
                "next_attempt_at",
                "updated_at",
            ]
        )
    mail.outbox.clear()
    output = StringIO()
    call_command(
        "deliver_membership_expiry_emails",
        gym_code="EXPIRY-CAP-CMD",
        max_attempts=3,
        stdout=output,
    )
    assert "Delivered 0" in output.getvalue()
    assert "failed 1" in output.getvalue()
    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.attempt_count == 4
    assert notification.last_error_code == PROVIDER_UNAVAILABLE
    assert notification.failed_at is not None
    assert notification.next_attempt_at is None
    assert mail.outbox == []


def test_legacy_delivery_backfill_satisfies_state_constraints() -> None:
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps

    tenant = _provision("EXPIRY-REPAIR")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        for number, address in (
            ("PEND", "pend@example.test"),
            ("EXHAUST", "exhaust@example.test"),
            ("SENT", "sent@example.test"),
            ("FAIL", "fail@example.test"),
            ("CANCEL", "cancel@example.test"),
        ):
            member = _member(
                tenant,
                number=number,
                first_name=number.title(),
                email=address,
                today=today,
            )
            _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 5
    rows = {
        notification.recipient_member.member_number: notification
        for notification in Notification.objects.filter(gym=tenant.gym).select_related(
            "recipient_member"
        )
    }
    scheduled = {number: row.scheduled_at for number, row in rows.items()}
    sent_at = timezone.now() - timedelta(hours=2)
    failed_at = timezone.now() - timedelta(hours=1)
    constraint_names = (
        "notification_attempt_bound_ck",
        "notification_error_code_ck",
        "notification_provider_id_ck",
        "notification_status_timestamps_ck",
    )
    with transaction.atomic():
        try:
            with connection.cursor() as cursor:
                for name in constraint_names:
                    cursor.execute(f"ALTER TABLE notifications_notification DROP CONSTRAINT {name}")
            Notification.objects.filter(pk=rows["PEND"].pk).update(
                status="PENDING",
                attempt_count=2,
                sent_at=None,
                failed_at=None,
                next_attempt_at=None,
                last_error_code="PROVIDER_UNAVAILABLE",
                provider_id="msg.123:ok",
            )
            Notification.objects.filter(pk=rows["EXHAUST"].pk).update(
                status="PENDING",
                attempt_count=8,
                sent_at=None,
                failed_at=None,
                next_attempt_at=None,
                last_error_code="smtp failed ann@example.test",
                provider_id="ann@example.test",
            )
            Notification.objects.filter(pk=rows["SENT"].pk).update(
                status="SENT",
                attempt_count=0,
                sent_at=sent_at,
                failed_at=None,
                next_attempt_at=timezone.now(),
                last_error_code="PROVIDER_UNAVAILABLE",
                provider_id="mailbox ann@example.test",
            )
            Notification.objects.filter(pk=rows["FAIL"].pk).update(
                status="FAILED",
                attempt_count=0,
                sent_at=None,
                failed_at=failed_at,
                next_attempt_at=timezone.now(),
                last_error_code="",
                provider_id="provider-secret",
            )
            Notification.objects.filter(pk=rows["CANCEL"].pk).update(
                status="CANCELLED",
                attempt_count=9,
                sent_at=None,
                failed_at=None,
                next_attempt_at=timezone.now(),
                last_error_code="RAW TEXT",
                provider_id="ann@example.test",
            )
            migration = import_module(
                "apps.notifications.migrations.0003_notification_delivery_state"
            )
            migration.backfill_notification_delivery_state(
                apps, SimpleNamespace(connection=connection)
            )
            repaired = {
                notification.recipient_member.member_number: notification
                for notification in Notification.objects.filter(gym=tenant.gym).select_related(
                    "recipient_member"
                )
            }
            pending = repaired["PEND"]
            assert pending.status == Notification.Status.PENDING
            assert pending.attempt_count == 2
            assert pending.next_attempt_at == scheduled["PEND"]
            assert pending.last_error_code == PROVIDER_UNAVAILABLE
            assert pending.provider_id == "msg.123:ok"
            assert pending.sent_at is None
            assert pending.failed_at is None
            assert "pend@example.test" not in pending.payload.values()

            exhausted = repaired["EXHAUST"]
            assert exhausted.status == Notification.Status.FAILED
            assert exhausted.attempt_count == 5
            assert exhausted.failed_at == scheduled["EXHAUST"]
            assert exhausted.sent_at is None
            assert exhausted.next_attempt_at is None
            assert exhausted.last_error_code == DELIVERY_REJECTED
            assert exhausted.provider_id == ""

            sent = repaired["SENT"]
            assert sent.status == Notification.Status.SENT
            assert sent.attempt_count == 1
            assert sent.sent_at == sent_at
            assert sent.failed_at is None
            assert sent.next_attempt_at is None
            assert sent.last_error_code == ""
            assert sent.provider_id == ""

            failed = repaired["FAIL"]
            assert failed.status == Notification.Status.FAILED
            assert failed.attempt_count == 1
            assert failed.failed_at == failed_at
            assert failed.sent_at is None
            assert failed.next_attempt_at is None
            assert failed.last_error_code == DELIVERY_REJECTED
            assert failed.provider_id == "provider-secret"

            cancelled = repaired["CANCEL"]
            assert cancelled.status == Notification.Status.CANCELLED
            assert cancelled.attempt_count == 5
            assert cancelled.sent_at is None
            assert cancelled.failed_at is None
            assert cancelled.next_attempt_at is None
            assert cancelled.last_error_code == ""
            assert cancelled.provider_id == ""

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM notifications_notification
                    WHERE NOT (
                      attempt_count <= 5
                      AND last_error_code ~ '^[A-Z0-9_]{0,32}$'
                      AND provider_id ~ '^[A-Za-z0-9_.:-]{0,255}$'
                      AND (
                        (
                          status = 'PENDING'
                          AND sent_at IS NULL
                          AND failed_at IS NULL
                          AND next_attempt_at IS NOT NULL
                          AND attempt_count < 5
                        )
                        OR (
                          status = 'SENT'
                          AND sent_at IS NOT NULL
                          AND failed_at IS NULL
                          AND next_attempt_at IS NULL
                          AND last_error_code = ''
                          AND attempt_count BETWEEN 1 AND 5
                        )
                        OR (
                          status = 'FAILED'
                          AND sent_at IS NULL
                          AND failed_at IS NOT NULL
                          AND next_attempt_at IS NULL
                          AND last_error_code ~ '^[A-Z][A-Z0-9_]{0,31}$'
                          AND attempt_count BETWEEN 1 AND 5
                        )
                        OR (
                          status = 'CANCELLED'
                          AND sent_at IS NULL
                          AND failed_at IS NULL
                          AND next_attempt_at IS NULL
                          AND last_error_code = ''
                          AND attempt_count <= 5
                        )
                      )
                    )
                    """
                )
                assert cursor.fetchone()[0] == 0
        finally:
            transaction.set_rollback(True)


def test_concurrent_delivery_workers_send_one_email() -> None:
    tenant = _provision("EXPIRY-RACE")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7])
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = _plan(tenant)
        member = _member(
            tenant, number="RACE-1", first_name="Race", email="race@example.test", today=today
        )
        _sell(tenant, member=member, plan=plan, warning_day=7, today=today)
    assert _schedule(tenant) == 1
    gym_id = tenant.gym.id
    SlowBackend.fail_silently_values.clear()
    SlowBackend.sent_keys.clear()
    barrier = threading.Barrier(2)

    def worker() -> DeliveryOutcome | None:
        connections.close_all()
        try:
            from django.conf import settings

            settings.EMAIL_BACKEND = "tests.mail_backends.SlowBackend"
            barrier.wait(timeout=5)
            with transaction.atomic():
                set_gym_context(gym_id=gym_id)
                gym = Gym.objects.get(pk=gym_id)
                return deliver_next_membership_expiry_email(gym=gym, today=gym_today(gym))
        finally:
            connections.close_all()

    with override_settings(EMAIL_BACKEND="tests.mail_backends.SlowBackend"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))

    assert sorted(result.value if result else "" for result in results) == ["", "SENT"]
    assert len(SlowBackend.sent_keys) == 1
    assert SlowBackend.fail_silently_values
    assert all(value is False for value in SlowBackend.fail_silently_values)
    notification = Notification.objects.get(gym_id=gym_id)
    assert notification.status == Notification.Status.SENT
    assert notification.attempt_count == 1

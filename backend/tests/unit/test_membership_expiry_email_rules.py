from __future__ import annotations

from datetime import UTC, date, datetime
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.notifications.services import (
    DELIVERY_REJECTED,
    PROVIDER_UNAVAILABLE,
    build_expiry_payload,
    delivery_error_code,
    header_safe,
    membership_expiry_deduplication_key,
    render_expiry_message,
    retry_delay_seconds,
    validate_expiry_payload,
)


def _payload() -> dict[str, str | int]:
    return build_expiry_payload(
        member_name="Ann Member",
        plan_name="Gold Year",
        expiry_date=date(2026, 10, 17),
        warning_day=0,
        gym_name="North Gym",
        gym_email="desk@example.test",
        gym_phone="+661234567",
    )


def test_payload_rejects_destination_and_unknown_fields() -> None:
    payload = _payload()
    validate_expiry_payload(payload)
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_expiry_payload({**payload, "email": "ann@example.test"})
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_expiry_payload({**payload, "destination": "ann@example.test"})
    with pytest.raises(ValueError, match="warning day"):
        validate_expiry_payload({**payload, "warning_day": True})
    with pytest.raises(ValueError, match="warning day"):
        validate_expiry_payload({**payload, "warning_day": 366})


def test_payload_omits_blank_gym_contacts_and_never_carries_member_email() -> None:
    payload = build_expiry_payload(
        member_name="Ann\r\nMember",
        plan_name="Gold Year",
        expiry_date=date(2026, 10, 24),
        warning_day=7,
        gym_name="North Gym",
        gym_email=" ",
        gym_phone="not-a-phone",
    )

    assert payload["member_name"] == "Ann Member"
    assert "gym_email" not in payload
    assert "gym_phone" not in payload
    assert "ann@example.test" not in payload.values()
    validate_expiry_payload(payload)


def test_header_safe_message_collapses_injection_and_stays_plain_text() -> None:
    sanitized = header_safe("Ann\r\nBcc: evil@example.test", max_length=80)
    assert sanitized == "Ann Bcc: evil@example.test"
    subject, body = render_expiry_message(
        member_name="Ann\nBcc: evil@example.test",
        plan_name="Gold Year",
        expiry_date=date(2026, 10, 17),
        gym_name="North Gym",
        gym_email="desk@example.test",
        gym_phone="+661234567",
    )

    assert "\n" not in subject
    assert "\r" not in subject
    assert "2026-10-17" in subject
    assert "Hello Ann Bcc: evil@example.test," in body
    assert "Gold Year" in body
    assert "desk@example.test" in body
    assert "+661234567" in body
    assert "<html" not in body.lower()


def test_retry_delay_is_bounded_and_errors_are_stable_codes() -> None:
    assert [retry_delay_seconds(attempt) for attempt in (1, 2, 3, 4)] == [300, 600, 1200, 2400]
    assert retry_delay_seconds(12) == 3600
    secret = ConnectionError("smtp failed for member@secret.example password=hunter2")
    assert delivery_error_code(secret) == PROVIDER_UNAVAILABLE
    assert "hunter2" not in delivery_error_code(secret)
    assert "secret.example" not in delivery_error_code(secret)
    rejected = delivery_error_code(RuntimeError("mailbox ann@example.test rejected"))
    assert rejected == DELIVERY_REJECTED


def test_dedup_key_binds_membership_expiry_warning_day_and_template() -> None:
    membership_id = uuid4()
    membership = SimpleNamespace(id=membership_id, expiry_date=date(2026, 10, 24))
    assert (
        membership_expiry_deduplication_key(membership=membership, warning_day=7)
        == f"membership-expiry:{membership_id}:2026-10-24:7:v1"
    )


def test_backfill_repair_drops_blank_contacts_and_destination_keys() -> None:
    migration = import_module("apps.notifications.migrations.0003_notification_delivery_state")
    repaired = migration.repair_expiry_payload(
        {
            "member_name": "Ann",
            "plan_name": "Gold Year",
            "expiry_date": "2026-10-17",
            "warning_day": 0,
            "gym_name": "North Gym",
            "gym_email": "",
            "gym_phone": "call me",
            "email": "ann@example.test",
            "to": "ann@example.test",
        }
    )

    assert repaired == {
        "member_name": "Ann",
        "plan_name": "Gold Year",
        "expiry_date": "2026-10-17",
        "warning_day": 0,
        "gym_name": "North Gym",
    }


def test_backfill_repairs_every_legacy_status_without_a_destination() -> None:
    migration = import_module("apps.notifications.migrations.0003_notification_delivery_state")
    scheduled = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
    sent_at = datetime(2026, 10, 17, 2, 0, tzinfo=UTC)
    failed_at = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
    due = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)

    pending = migration.repair_notification_delivery_fields(
        status="PENDING",
        attempt_count=2,
        sent_at=sent_at,
        failed_at=None,
        scheduled_at=scheduled,
        next_attempt_at=due,
        last_error_code="PROVIDER_UNAVAILABLE",
        provider_id="msg.123:ok",
    )
    assert pending == {
        "status": "PENDING",
        "attempt_count": 2,
        "sent_at": None,
        "failed_at": None,
        "next_attempt_at": due,
        "last_error_code": "PROVIDER_UNAVAILABLE",
        "provider_id": "msg.123:ok",
    }

    undated = migration.repair_notification_delivery_fields(
        status="PENDING",
        attempt_count=0,
        sent_at=None,
        failed_at=None,
        scheduled_at=scheduled,
        next_attempt_at=None,
        last_error_code="smtp failed ann@example.test",
        provider_id="ann@example.test",
    )
    assert undated["status"] == "PENDING"
    assert undated["attempt_count"] == 0
    assert undated["next_attempt_at"] == scheduled
    assert undated["last_error_code"] == ""
    assert undated["provider_id"] == ""

    exhausted = migration.repair_notification_delivery_fields(
        status="PENDING",
        attempt_count=8,
        sent_at=None,
        failed_at=None,
        scheduled_at=scheduled,
        next_attempt_at=due,
        last_error_code="smtp failed ann@example.test",
        provider_id="ann@example.test",
    )
    assert exhausted == {
        "status": "FAILED",
        "attempt_count": 5,
        "sent_at": None,
        "failed_at": scheduled,
        "next_attempt_at": None,
        "last_error_code": "DELIVERY_REJECTED",
        "provider_id": "",
    }

    sent = migration.repair_notification_delivery_fields(
        status="SENT",
        attempt_count=0,
        sent_at=sent_at,
        failed_at=failed_at,
        scheduled_at=scheduled,
        next_attempt_at=due,
        last_error_code="PROVIDER_UNAVAILABLE",
        provider_id="mailbox ann@example.test",
    )
    assert sent == {
        "status": "SENT",
        "attempt_count": 1,
        "sent_at": sent_at,
        "failed_at": None,
        "next_attempt_at": None,
        "last_error_code": "",
        "provider_id": "",
    }

    failed = migration.repair_notification_delivery_fields(
        status="FAILED",
        attempt_count=0,
        sent_at=sent_at,
        failed_at=failed_at,
        scheduled_at=scheduled,
        next_attempt_at=due,
        last_error_code="",
        provider_id="provider-secret",
    )
    assert failed == {
        "status": "FAILED",
        "attempt_count": 1,
        "sent_at": None,
        "failed_at": failed_at,
        "next_attempt_at": None,
        "last_error_code": "DELIVERY_REJECTED",
        "provider_id": "provider-secret",
    }

    over_count = migration.repair_notification_delivery_fields(
        status="FAILED",
        attempt_count=12,
        sent_at=None,
        failed_at=None,
        scheduled_at=scheduled,
        next_attempt_at=None,
        last_error_code="mailbox ann@example.test",
        provider_id="id with space",
    )
    assert over_count["attempt_count"] == 5
    assert over_count["failed_at"] == scheduled
    assert over_count["last_error_code"] == "DELIVERY_REJECTED"
    assert over_count["provider_id"] == ""

    cancelled = migration.repair_notification_delivery_fields(
        status="CANCELLED",
        attempt_count=9,
        sent_at=sent_at,
        failed_at=failed_at,
        scheduled_at=scheduled,
        next_attempt_at=due,
        last_error_code="RAW TEXT",
        provider_id="ann@example.test",
    )
    assert cancelled == {
        "status": "CANCELLED",
        "attempt_count": 5,
        "sent_at": None,
        "failed_at": None,
        "next_attempt_at": None,
        "last_error_code": "",
        "provider_id": "",
    }

    for repaired_row in (pending, undated, exhausted, sent, failed, over_count, cancelled):
        assert "@" not in str(repaired_row["provider_id"])
        assert "@" not in str(repaired_row["last_error_code"])


def test_delivery_backfill_precedes_new_constraints() -> None:
    migration = import_module("apps.notifications.migrations.0003_notification_delivery_state")
    operations = migration.Migration.operations
    repair_at = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migration.migrations.RunPython)
        and operation.code is migration.backfill_notification_delivery_state
    )
    constraint_at = [
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migration.migrations.AddConstraint | migration.migrations.RunSQL)
    ]
    assert constraint_at
    assert repair_at < min(constraint_at)
    added_fields = [
        operation.name
        for operation in operations[:repair_at]
        if isinstance(operation, migration.migrations.AddField)
    ]
    assert added_fields == ["last_error_code", "next_attempt_at"]


def test_notification_http_routes_stay_unpublished() -> None:
    paths: list[str] = []

    def walk(patterns: list[URLPattern | URLResolver], prefix: str = "") -> None:
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(list(pattern.url_patterns), route)
            else:
                paths.append(route)

    walk(list(get_resolver().url_patterns))
    assert not any("notification" in path for path in paths)

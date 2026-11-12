from types import SimpleNamespace

from apps.notifications.push_services import classify_push_fanout, device_send_is_open
from apps.notifications.services import DeliveryOutcome


def test_parent_outcome_retries_only_while_a_device_is_still_open() -> None:
    assert classify_push_fanout(eligible=0, accepted=0, retryable=0) is DeliveryOutcome.CANCELLED
    assert classify_push_fanout(eligible=2, accepted=1, retryable=1) is DeliveryOutcome.RETRIED
    assert classify_push_fanout(eligible=2, accepted=2, retryable=0) is DeliveryOutcome.SENT
    assert classify_push_fanout(eligible=1, accepted=1, retryable=0) is DeliveryOutcome.SENT
    assert classify_push_fanout(eligible=2, accepted=1, retryable=0) is DeliveryOutcome.SENT
    assert classify_push_fanout(eligible=2, accepted=0, retryable=0) is DeliveryOutcome.FAILED


def test_an_accepted_or_invalid_device_is_not_sent_again() -> None:
    assert device_send_is_open(None, max_attempts=5) is True
    sent = SimpleNamespace(status="SENT", last_error_code="", attempt_count=1)
    invalid = SimpleNamespace(status="FAILED", last_error_code="INVALID_TOKEN", attempt_count=1)
    retry = SimpleNamespace(
        status="FAILED",
        last_error_code="PROVIDER_UNAVAILABLE",
        attempt_count=1,
    )
    exhausted = SimpleNamespace(
        status="FAILED",
        last_error_code="DELIVERY_REJECTED",
        attempt_count=5,
    )
    assert device_send_is_open(sent, max_attempts=5) is False  # type: ignore[arg-type]
    assert device_send_is_open(invalid, max_attempts=5) is False  # type: ignore[arg-type]
    assert device_send_is_open(retry, max_attempts=5) is True  # type: ignore[arg-type]
    assert device_send_is_open(exhausted, max_attempts=5) is False  # type: ignore[arg-type]

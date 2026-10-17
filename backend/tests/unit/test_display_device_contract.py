from __future__ import annotations

from apps.members.device_credentials import (
    format_device_credential,
    new_device_secret,
    new_device_selector,
    parse_device_credential,
)
from apps.members.device_serializers import (
    DeviceIngestSerializer,
    DisplayDeviceEmptySerializer,
    DisplayDeviceEnrollSerializer,
    IntegrationDeviceEnrollSerializer,
)


def test_device_credential_parser_is_bounded() -> None:
    selector = new_device_selector()
    secret = new_device_secret()
    credential = format_device_credential(selector=selector, secret=secret)
    parsed = parse_device_credential(credential)
    assert parsed is not None
    assert parsed.selector == selector
    assert parsed.secret == secret
    assert parse_device_credential(f"Bearer {credential}") is None
    assert parse_device_credential(credential + " ") is None
    assert parse_device_credential("gms-device:v1:short.secret") is None
    assert parse_device_credential("x" * 221) is None


def test_device_serializers_reject_selectors_limits_and_actors() -> None:
    enroll = DisplayDeviceEnrollSerializer(
        data={"label": "Front door", "location": "Lobby", "gym_id": "gym", "credential": "secret"}
    )
    assert not enroll.is_valid()
    assert set(enroll.errors) == {"gym_id", "credential"}
    issue = DisplayDeviceEmptySerializer(
        data={
            "gym_id": "gym",
            "device_id": "device",
            "display_device_id": "device",
            "max_uses": 4,
            "ttl_seconds": 60,
            "display_location": "Side door",
        }
    )
    assert not issue.is_valid()
    assert set(issue.errors) == {
        "gym_id",
        "device_id",
        "display_device_id",
        "max_uses",
        "ttl_seconds",
        "display_location",
    }
    assert DisplayDeviceEmptySerializer(data={}).is_valid()
    ingest = DeviceIngestSerializer(
        data={
            "member_id": "00000000-0000-4000-8000-000000000001",
            "event_id": "event-id-000000001",
            "method": "QR",
            "template": "finger",
            "nfc_secret": "card",
            "source_metadata": {"raw": "capture"},
        }
    )
    assert not ingest.is_valid()
    assert set(ingest.errors) >= {"method", "template", "nfc_secret", "source_metadata"}
    integration = IntegrationDeviceEnrollSerializer(
        data={"label": "Door", "location": "Lobby", "kind": "DISPLAY"}
    )
    assert not integration.is_valid()
    assert set(integration.errors) == {"kind"}

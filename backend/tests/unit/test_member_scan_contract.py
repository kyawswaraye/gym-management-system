from __future__ import annotations

from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX
from apps.members.scan_services import payload_is_well_formed
from apps.members.serializers import MemberCheckInScanSerializer, TrainerCheckInScanSerializer


def test_scan_payload_must_match_the_issued_shape() -> None:
    suffix = "abcdefghijklmnopqrstuvwxyz0123456789ABCDE"
    assert payload_is_well_formed(f"{CHALLENGE_PAYLOAD_PREFIX}{suffix}")
    assert not payload_is_well_formed(suffix)
    assert not payload_is_well_formed(f"{CHALLENGE_PAYLOAD_PREFIX}short")
    assert not payload_is_well_formed(f"{CHALLENGE_PAYLOAD_PREFIX}{suffix} has space")
    assert not payload_is_well_formed("x" * 129)


def test_member_scan_serializer_rejects_selectors_and_actors() -> None:
    payload = f"{CHALLENGE_PAYLOAD_PREFIX}abcdefghijklmnopqrstuvwxyz0123456789ABCDE"
    accepted = MemberCheckInScanSerializer(data={"payload": payload})
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data == {"payload": payload}
    rejected = MemberCheckInScanSerializer(
        data={
            "payload": payload,
            "member_id": "11111111-1111-1111-1111-111111111111",
            "gym_id": "22222222-2222-2222-2222-222222222222",
            "membership_id": "33333333-3333-3333-3333-333333333333",
            "method": "QR",
            "occurred_at": "2026-10-17T00:00:00Z",
            "device_id": "44444444-4444-4444-4444-444444444444",
            "source_metadata": {"desk": "front"},
            "status": "OPEN",
            "created_by_membership_id": "55555555-5555-5555-5555-555555555555",
            "idempotency_key": "scan-key-1234567890",
        }
    )
    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "member_id",
        "gym_id",
        "membership_id",
        "method",
        "occurred_at",
        "device_id",
        "source_metadata",
        "status",
        "created_by_membership_id",
        "idempotency_key",
    }


def test_trainer_scan_serializer_accepts_only_payload_and_member() -> None:
    payload = f"{CHALLENGE_PAYLOAD_PREFIX}abcdefghijklmnopqrstuvwxyz0123456789ABCDE"
    member_id = "11111111-1111-1111-1111-111111111111"
    accepted = TrainerCheckInScanSerializer(data={"payload": payload, "member_id": member_id})
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["payload"] == payload
    assert str(accepted.validated_data["member_id"]) == member_id
    rejected = TrainerCheckInScanSerializer(
        data={
            "payload": payload,
            "member_id": member_id,
            "gym_id": "22222222-2222-2222-2222-222222222222",
            "method": "QR",
            "status": "OPEN",
            "recorded_by_membership_id": "33333333-3333-3333-3333-333333333333",
            "created_by_membership_id": "44444444-4444-4444-4444-444444444444",
            "source_metadata": {"desk": "front"},
            "idempotency_key": "trainer-scan-key-1234",
            "device_id": "55555555-5555-5555-5555-555555555555",
        }
    )
    assert not rejected.is_valid()
    assert "payload" not in rejected.errors
    assert "member_id" not in rejected.errors
    assert "gym_id" in rejected.errors
    assert "recorded_by_membership_id" in rejected.errors

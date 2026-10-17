from __future__ import annotations

from rest_framework import serializers

from apps.members.serializers import OpaqueCursorField
from apps.users.serializers import StrictSerializer


class DisplayDeviceEnrollSerializer(StrictSerializer):
    label = serializers.CharField(max_length=80)
    location = serializers.CharField(max_length=80)


class IntegrationDeviceEnrollSerializer(StrictSerializer):
    label = serializers.CharField(max_length=80)
    location = serializers.CharField(max_length=80)
    kind = serializers.ChoiceField(choices=("NFC", "BIOMETRIC"))


class DeviceIngestSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    event_id = serializers.RegexField(r"^[A-Za-z0-9._:-]{16,128}$")


class DeviceIngestResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    method = serializers.CharField()
    check_in_at = serializers.DateTimeField()
    status = serializers.CharField()


class DeviceIngestEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = DeviceIngestResultSerializer()
    message = serializers.CharField()


class DisplayDeviceEmptySerializer(StrictSerializer):
    """Rotate, revoke, and device issuance accept no selectors or limits."""


class DisplayDeviceListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = OpaqueCursorField(required=False, allow_blank=False, max_length=2048)


class DisplayDeviceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    label = serializers.CharField()
    location = serializers.CharField()
    kind = serializers.CharField()
    status = serializers.CharField()
    credential_selector = serializers.CharField()
    credential_version = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    rotated_at = serializers.DateTimeField(allow_null=True)
    revoked_at = serializers.DateTimeField(allow_null=True)


class DisplayDeviceCredentialSerializer(DisplayDeviceSerializer):
    credential = serializers.CharField()


class DisplayDeviceEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = DisplayDeviceSerializer()
    message = serializers.CharField()


class DisplayDeviceCredentialEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = DisplayDeviceCredentialSerializer()
    message = serializers.CharField()


class DisplayDeviceCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class DisplayDeviceListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = DisplayDeviceSerializer(many=True)
    meta = DisplayDeviceCollectionMetaSerializer()
    message = serializers.CharField()

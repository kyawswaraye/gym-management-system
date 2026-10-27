from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.tenants.models import GymMembership, StaffProfile
from apps.users.serializers import StrictSerializer, validate_new_password


def _commission(**overrides: Any) -> serializers.DecimalField:
    options: dict[str, Any] = {
        "max_digits": 5,
        "decimal_places": 2,
        "min_value": Decimal("0"),
        "max_value": Decimal("100"),
    }
    options.update(overrides)
    return serializers.DecimalField(**options)


def _staff_code(value: str) -> str:
    code = value.strip()
    if not code or any(character.isspace() for character in code):
        raise serializers.ValidationError("Enter a staff code without spaces.")
    return code


class StaffMembershipSerializer(serializers.Serializer):
    gym_membership_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    status = serializers.CharField()
    is_owner = serializers.BooleanField()
    authz_version = serializers.IntegerField(min_value=1)
    invited_at = serializers.DateTimeField(allow_null=True)
    activated_at = serializers.DateTimeField(allow_null=True)


class StaffSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    staff_code = serializers.CharField()
    display_name = serializers.CharField()
    phone = serializers.CharField()
    joined_date = serializers.DateField()
    status = serializers.ChoiceField(choices=StaffProfile.Status.choices)
    employment_title = serializers.CharField()
    default_commission_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    terminated_date = serializers.DateField(allow_null=True)
    archived_at = serializers.DateTimeField(allow_null=True)
    membership = StaffMembershipSerializer()
    role_ids = serializers.ListField(child=serializers.UUIDField())
    invited_role_ids = serializers.ListField(child=serializers.UUIDField())


class StaffInviteInputSerializer(StrictSerializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    staff_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=32)
    display_name = serializers.CharField(max_length=150)
    joined_date = serializers.DateField()
    employment_title = serializers.CharField(required=False, allow_blank=True, max_length=100)
    default_commission_percent = _commission(required=False)
    role_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=20)

    def validate_first_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_last_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_display_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_staff_code(self, value: str) -> str:
        if value.strip() == "":
            return ""
        return _staff_code(value)

    def validate_phone(self, value: str) -> str:
        return value.strip()

    def validate_employment_title(self, value: str) -> str:
        return value.strip()

    def validate_role_ids(self, value: list[Any]) -> list[Any]:
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Role ids must be unique.")
        return value


class StaffAcceptInputSerializer(StrictSerializer):
    invitation_token = serializers.CharField(trim_whitespace=False, write_only=True)
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    password_confirmation = serializers.CharField(trim_whitespace=False, write_only=True)
    first_name = serializers.CharField(required=False, max_length=150)
    last_name = serializers.CharField(required=False, max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    display_name = serializers.CharField(required=False, max_length=150)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        validate_new_password(
            str(attrs["password"]),
            str(attrs["password_confirmation"]),
        )
        return attrs

    def validate_first_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_last_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_display_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_phone(self, value: str) -> str:
        return value.strip()


class StaffUpdateSerializer(StrictSerializer):
    staff_code = serializers.CharField(required=False, max_length=32)
    display_name = serializers.CharField(required=False, max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    joined_date = serializers.DateField(required=False)
    employment_title = serializers.CharField(required=False, allow_blank=True, max_length=100)
    default_commission_percent = _commission(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one profile field is required.")
        return attrs

    def validate_staff_code(self, value: str) -> str:
        return _staff_code(value)

    def validate_display_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_phone(self, value: str) -> str:
        return value.strip()

    def validate_employment_title(self, value: str) -> str:
        return value.strip()


class StaffLifecycleSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=3, max_length=500, trim_whitespace=True)


class StaffListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=StaffProfile.Status.choices, required=False)
    membership_status = serializers.ChoiceField(
        choices=GymMembership.Status.choices, required=False
    )
    search = serializers.CharField(required=False, allow_blank=False, max_length=100)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)

    def validate_search(self, value: str) -> str:
        search = value.strip()
        if not search:
            raise serializers.ValidationError("Search must not be blank.")
        return search


class StaffInvitationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()
    role_ids = serializers.ListField(child=serializers.UUIDField())
    membership = StaffMembershipSerializer()
    staff = StaffSerializer()


class StaffSessionSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    access_expires_at = serializers.DateTimeField()
    refresh_token = serializers.CharField()
    refresh_expires_at = serializers.DateTimeField()
    requires_gym_selection = serializers.BooleanField()


class StaffListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class StaffListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = StaffSerializer(many=True)
    meta = StaffListMetaSerializer()
    message = serializers.CharField()


class StaffEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = StaffSerializer()
    message = serializers.CharField()


class StaffInvitationEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = StaffInvitationSerializer()
    message = serializers.CharField()


class StaffSessionEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = StaffSessionSerializer()
    message = serializers.CharField()

from __future__ import annotations

from rest_framework import serializers


class GymDisplaySerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class GymMembershipChoiceSerializer(serializers.Serializer):
    gym_membership_id = serializers.UUIDField()
    gym_id = serializers.UUIDField()
    gym = GymDisplaySerializer()


class GymMembershipRowSerializer(GymMembershipChoiceSerializer):
    status = serializers.CharField()
    is_owner = serializers.BooleanField()
    authz_version = serializers.IntegerField(min_value=1)


class TokenPairSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    access_expires_at = serializers.DateTimeField()
    refresh_token = serializers.CharField()
    refresh_expires_at = serializers.DateTimeField()


class DirectLoginDataSerializer(TokenPairSerializer):
    requires_gym_selection = serializers.BooleanField(default=False)
    available_gym_memberships = GymMembershipChoiceSerializer(many=True)


class SelectionLoginDataSerializer(serializers.Serializer):
    requires_gym_selection = serializers.BooleanField(default=True)
    gym_selection_ticket = serializers.CharField()
    gym_selection_expires_at = serializers.DateTimeField()
    available_gym_memberships = GymMembershipChoiceSerializer(many=True)


class DirectLoginEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = DirectLoginDataSerializer()
    message = serializers.CharField()


class SelectionLoginEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = SelectionLoginDataSerializer()
    message = serializers.CharField()


class TokenEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = TokenPairSerializer()
    message = serializers.CharField()


class GymMembershipListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = GymMembershipRowSerializer(many=True)
    message = serializers.CharField()


class CurrentUserIdentitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class SelectedGymSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    currency = serializers.CharField()
    timezone = serializers.CharField()


class SelectedMembershipSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    is_owner = serializers.BooleanField()
    roles = serializers.ListField(child=serializers.CharField())


class FeatureEntitlementSerializer(serializers.Serializer):
    feature__code = serializers.CharField()
    limit_value = serializers.IntegerField(allow_null=True)


class CurrentUserDataSerializer(serializers.Serializer):
    user = CurrentUserIdentitySerializer()
    gym = SelectedGymSerializer()
    gym_membership = SelectedMembershipSerializer()
    permissions = serializers.ListField(child=serializers.CharField())
    features = FeatureEntitlementSerializer(many=True)
    authz_version = serializers.IntegerField(min_value=1)


class CurrentUserEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = CurrentUserDataSerializer()
    message = serializers.CharField()

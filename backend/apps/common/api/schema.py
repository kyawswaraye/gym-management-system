from __future__ import annotations

from collections.abc import Callable
from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers

Endpoint = tuple[str, str, str, Callable[..., Any]]


def phase_1a_endpoints(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Publish foundation, Phase 1B, MembershipPlan, and Member CRUD/archive routes."""

    return [
        endpoint
        for endpoint in endpoints
        if endpoint[0] in {"/api/v1/health/", "/api/v1/readiness/"}
        or endpoint[0].startswith("/api/v1/auth/")
        or endpoint[0]
        in {
            "/api/v1/admin/audit-logs/",
            "/api/v1/audit-logs/",
            "/api/v1/admin/dashboard/",
            "/api/v1/admin/gyms/",
            "/api/v1/admin/gyms/{gym_id}/",
            "/api/v1/admin/gyms/{gym_id}/activate/",
            "/api/v1/admin/gyms/{gym_id}/owners/",
            "/api/v1/admin/gyms/{gym_id}/suspend/",
            "/api/v1/admin/subscriptions/",
            "/api/v1/admin/subscriptions/{id}/",
            "/api/v1/gym/",
            "/api/v1/gym-memberships/{id}/roles/",
            "/api/v1/owners/",
            "/api/v1/settings/",
            "/api/v1/owners/invitations/",
            "/api/v1/owners/invitations/accept/",
            "/api/v1/owners/{gym_membership_id}/revoke/",
            "/api/v1/permissions/",
            "/api/v1/staff/",
            "/api/v1/staff/invitations/",
            "/api/v1/staff/invitations/accept/",
            "/api/v1/staff/{id}/",
            "/api/v1/staff/{id}/activate/",
            "/api/v1/staff/{id}/suspend/",
            "/api/v1/members/",
            "/api/v1/members/{id}/",
            "/api/v1/members/{id}/archive/",
            "/api/v1/membership-plans/",
            "/api/v1/membership-plans/{id}/",
            "/api/v1/membership-plans/{id}/archive/",
            "/api/v1/roles/",
            "/api/v1/roles/{id}/",
            "/api/v1/roles/{id}/archive/",
            "/api/v1/roles/{id}/permissions/",
        }
    ]


class StaffJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.authentication.StaffJWTAuthentication"
    name = "staffBearerAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class PlatformJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.authentication.PlatformJWTAuthentication"
    name = "platformBearerAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class StaffOrPlatformJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.authentication.StaffOrPlatformJWTAuthentication"
    name = "staffOrPlatformBearerAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(required=False)


class ErrorEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=False)
    error = ErrorDetailSerializer()
    request_id = serializers.CharField()


class HealthDataSerializer(serializers.Serializer):
    status = serializers.CharField()


class HealthEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = HealthDataSerializer()
    message = serializers.CharField()


class ReadinessDataSerializer(serializers.Serializer):
    status = serializers.CharField()
    database = serializers.CharField()
    jwt_keys = serializers.BooleanField()


class ReadinessEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = ReadinessDataSerializer()
    message = serializers.CharField()

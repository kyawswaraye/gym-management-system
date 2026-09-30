from __future__ import annotations

from collections.abc import Callable
from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers

Endpoint = tuple[str, str, str, Callable[..., Any]]


def phase_1a_endpoints(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Publish only the implemented Phase 1A foundation contract."""

    return [
        endpoint
        for endpoint in endpoints
        if endpoint[0] in {"/api/v1/health/", "/api/v1/readiness/"}
        or endpoint[0].startswith("/api/v1/auth/")
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

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers

Endpoint = tuple[str, str, str, Callable[..., Any]]


def phase_1a_endpoints(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Publish foundation, operations, and the cash-book routes."""

    return [
        endpoint
        for endpoint in endpoints
        if endpoint[0] in {"/api/v1/health/", "/api/v1/readiness/"}
        or endpoint[0].startswith("/api/v1/auth/")
        or endpoint[0].startswith("/api/v1/member-auth/")
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
            "/api/v1/members/{id}/summary/",
            "/api/v1/members/{id}/payments/",
            "/api/v1/members/{id}/check-ins/",
            "/api/v1/check-ins/",
            "/api/v1/check-ins/{id}/",
            "/api/v1/check-ins/{id}/check-out/",
            "/api/v1/check-in-challenges/",
            "/api/v1/check-in-display-devices/",
            "/api/v1/check-in-display-devices/{id}/rotate-credential/",
            "/api/v1/check-in-display-devices/{id}/revoke/",
            "/api/v1/device/check-in-challenges/",
            "/api/v1/check-in-integration-devices/",
            "/api/v1/check-in-integration-devices/{id}/revoke/",
            "/api/v1/check-in-integration-devices/{id}/rotate-credential/",
            "/api/v1/device/check-ins/",
            "/api/v1/member/check-ins/scan/",
            "/api/v1/membership-plans/",
            "/api/v1/membership-plans/{id}/",
            "/api/v1/membership-plans/{id}/archive/",
            "/api/v1/memberships/",
            "/api/v1/memberships/expiry/",
            "/api/v1/memberships/{id}/",
            "/api/v1/memberships/{id}/cancel/",
            "/api/v1/memberships/{id}/resume/",
            "/api/v1/memberships/{id}/suspend/",
            "/api/v1/roles/",
            "/api/v1/roles/{id}/",
            "/api/v1/roles/{id}/archive/",
            "/api/v1/roles/{id}/permissions/",
            "/api/v1/files/upload-intents/",
            "/api/v1/files/{id}/complete/",
            "/api/v1/files/{id}/download-url/",
            "/api/v1/files/{id}/",
            "/api/v1/cash-sessions/",
            "/api/v1/cash-sessions/open/",
            "/api/v1/cash-sessions/{id}/",
            "/api/v1/cash-sessions/{id}/adjustments/",
            "/api/v1/cash-sessions/{id}/reconcile/",
            "/api/v1/cash-transactions/",
            "/api/v1/payments/",
            "/api/v1/payments/{payment_id}/",
            "/api/v1/payments/{payment_id}/submit/",
            "/api/v1/payments/{payment_id}/approve/",
            "/api/v1/payments/{payment_id}/reject/",
            "/api/v1/payments/{payment_id}/cancel/",
            "/api/v1/receipts/",
            "/api/v1/receipts/{receipt_id}/",
            "/api/v1/receipts/{receipt_id}/download/",
            "/api/v1/sales/daily/",
            "/api/v1/expense-categories/",
            "/api/v1/expense-categories/{category_id}/",
            "/api/v1/expenses/",
            "/api/v1/expenses/{expense_id}/",
            "/api/v1/expenses/{expense_id}/submit/",
            "/api/v1/expenses/{expense_id}/approve/",
            "/api/v1/expenses/{expense_id}/reject/",
            "/api/v1/expenses/{expense_id}/cancel/",
            "/api/v1/salary-periods/",
            "/api/v1/salary-periods/{period_id}/",
            "/api/v1/salary-periods/{period_id}/lines/",
            "/api/v1/salary-periods/{period_id}/submit/",
            "/api/v1/salary-periods/{period_id}/approve/",
            "/api/v1/salary-periods/{period_id}/reject/",
            "/api/v1/salary-periods/{period_id}/cancel/",
            "/api/v1/product-categories/",
            "/api/v1/products/",
            "/api/v1/products/{product_id}/",
            "/api/v1/products/low-stock/",
            "/api/v1/stock-movements/",
            "/api/v1/stock-movements/adjust/",
        }
    ]


class MemberJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.authentication.MemberJWTAuthentication"
    name = "memberBearerAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class StaffJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.authentication.StaffJWTAuthentication"
    name = "staffBearerAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class StaffFileAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.files.authentication.StaffFileAuthentication"
    name = "staffFileBearerAuth"

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

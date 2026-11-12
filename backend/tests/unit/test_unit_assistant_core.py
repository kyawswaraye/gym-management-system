from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.access import call_authorized_service
from apps.assistant.configuration import (
    ProviderFailure,
    assistant_configuration,
    classify_provider_failure,
    gemini_max_retries,
    max_steps,
)
from apps.assistant.identity import execution_identity
from apps.assistant.prerequisites import FieldRequirement, Prerequisite, missing_fields
from apps.assistant.registry import ToolDefinition, closed_production_names, production_registry
from apps.members.device_views import DisplayDeviceRevokeView
from apps.tenants.exceptions import MFARequired
from apps.tenants.views import OwnerInvitationCreateView
from apps.users.models import RefreshSession, User
from apps.users.permissions import RequiresRecentMFA


def test_production_registry_registers_tested_write_tools() -> None:
    names = production_registry().names()
    assert names == closed_production_names()
    assert "create_payment" in names
    assert "approve_payment" in names
    assert "reject_payment" in names
    assert "create_retail_sale" in names
    assert "approve_retail_sale" in names
    assert "reject_retail_sale" in names
    assert "create_salary_period" in names
    assert "approve_salary_period" in names
    assert "reject_salary_period" in names
    assert "create_pt_sale" in names
    assert "approve_pt_sale" in names
    assert "reject_pt_sale" in names
    assert "invite_owner" not in names
    assert "invite_staff" not in names
    assert "revoke_display_device" not in names


def test_registry_keeps_external_effects_visible() -> None:
    registry = production_registry()
    registry.register(
        ToolDefinition(
            name="invite_owner",
            description="Invite an owner",
            module="owners",
            operation="write",
            permissions=("staff.manage",),
            requires_recent_mfa=True,
            external_side_effects=("email",),
        )
    )
    tool = registry.get("invite_owner")
    assert tool.external_side_effects == ("email",)
    assert not hasattr(tool, "covers_external_side_effects")


def test_missing_prerequisite_fields_come_from_the_definition() -> None:
    prerequisite = Prerequisite(
        code="example",
        description="Example",
        fields=(FieldRequirement("first_name", "First name"), FieldRequirement("phone", "Phone")),
    )
    assert missing_fields(prerequisite, {"first_name": "Maung", "phone": ""}) == ("phone",)
    assert missing_fields(prerequisite, {"first_name": "Maung", "phone": "09123"}) == ()


def test_execution_identity_uses_the_server_gym_and_canonical_payload() -> None:
    gym_id = uuid4()
    other_gym = uuid4()
    goal_id = uuid4()
    step_id = uuid4()
    left = execution_identity(
        gym_id=gym_id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name="create_member",
        attempt_number=1,
        payload={"gym_id": "client-value", "b": 1, "a": "Maung"},
    )
    right = execution_identity(
        gym_id=gym_id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name="create_member",
        attempt_number=1,
        payload={"a": "Maung", "b": 1, "gym_id": "client-value"},
    )
    moved = execution_identity(
        gym_id=other_gym,
        goal_id=goal_id,
        step_id=step_id,
        tool_name="create_member",
        attempt_number=1,
        payload={"a": "Maung", "b": 1, "gym_id": "client-value"},
    )
    assert left == right
    assert left != moved


def test_assistant_stays_unavailable_without_a_provider_prefix() -> None:
    with override_settings(ASSISTANT_ENABLED="true", GEMINI_API_KEY="", GEMINI_MODEL=""):
        assert assistant_configuration().available is False
    key = "k" * 20
    with override_settings(
        ASSISTANT_ENABLED="true",
        GEMINI_API_KEY=key,
        GEMINI_MODEL="gemini-test",
    ):
        assert assistant_configuration().available is True
    with override_settings(
        ASSISTANT_ENABLED="true",
        GEMINI_API_KEY=f"{key} ",
        GEMINI_MODEL="gemini-test",
    ):
        assert assistant_configuration().available is False
    with override_settings(
        ASSISTANT_ENABLED="maybe",
        GEMINI_API_KEY=key,
        GEMINI_MODEL="gemini-test",
    ):
        configuration = assistant_configuration()
        assert configuration.available is False
        assert "invalid_flag" in configuration.reasons


def test_provider_failures_are_classified_without_a_key_prefix() -> None:
    assert (
        classify_provider_failure(status_code=401, provider_status="") is ProviderFailure.CREDENTIAL
    )
    assert (
        classify_provider_failure(status_code=200, provider_status="PERMISSION_DENIED")
        is ProviderFailure.CREDENTIAL
    )
    assert classify_provider_failure(status_code=429, provider_status="") is ProviderFailure.QUOTA
    assert classify_provider_failure(status_code=408, provider_status="") is ProviderFailure.TIMEOUT
    assert classify_provider_failure(status_code=503, provider_status="") is ProviderFailure.NETWORK
    assert (
        classify_provider_failure(status_code=500, provider_status="INTERNAL")
        is ProviderFailure.PROVIDER
    )


def test_step_budget_defaults_to_four_and_retries_stay_at_one() -> None:
    with override_settings(ASSISTANT_MAX_STEPS="4"):
        assert max_steps() == 4
    with override_settings(ASSISTANT_MAX_STEPS=""):
        assert max_steps() == 4
    with override_settings(ASSISTANT_MAX_STEPS="21"):
        assert max_steps() == 4
    with override_settings(ASSISTANT_MAX_STEPS="20"):
        assert max_steps() == 20
    with override_settings(ASSISTANT_MAX_STEPS="12"):
        assert max_steps() == 12
    with override_settings(GEMINI_MAX_RETRIES="1"):
        assert gemini_max_retries() == 1
    with override_settings(GEMINI_MAX_RETRIES="5"):
        assert gemini_max_retries() == 1
    with override_settings(GEMINI_MAX_RETRIES="0"):
        assert gemini_max_retries() == 1


def _request(*, enabled: bool, verified_at: object) -> Request:
    user = User(email="owner@example.test", mfa_enabled=enabled)
    session = RefreshSession(mfa_verified_at=verified_at)
    request = Request(APIRequestFactory().get("/api/v1/assistant/status/"))
    request.user = user
    request.refresh_session = session
    request.permission_codes = frozenset({"staff.manage"})
    return request


def test_recent_mfa_result_matches_the_original_views() -> None:
    stale = _request(enabled=True, verified_at=timezone.now() - timedelta(hours=2))
    views: tuple[APIView, ...] = (
        APIView(),
        OwnerInvitationCreateView(),
        DisplayDeviceRevokeView(),
    )
    with override_settings(MFA_ENFORCED=True):
        for view in views:
            with pytest.raises(MFARequired):
                RequiresRecentMFA().has_permission(stale, view)
    fresh = _request(enabled=True, verified_at=timezone.now())
    disabled = _request(enabled=False, verified_at=timezone.now())
    with override_settings(MFA_ENFORCED=True):
        assert RequiresRecentMFA().has_permission(fresh, views[0]) is True
        with pytest.raises(MFARequired):
            RequiresRecentMFA().has_permission(disabled, views[1])
    with override_settings(MFA_ENFORCED=False):
        assert RequiresRecentMFA().has_permission(disabled, views[2]) is True


def test_missing_permission_or_session_does_not_call_the_service() -> None:
    tool = ToolDefinition(
        name="revoke_owner",
        description="Revoke an owner",
        module="owners",
        operation="write",
        permissions=("staff.manage",),
        requires_recent_mfa=True,
    )
    entitled = ToolDefinition(
        name="invite_owner",
        description="Invite an owner",
        module="owners",
        operation="write",
        permissions=("staff.manage",),
        features=("staff",),
        requires_recent_mfa=True,
    )
    called = {"count": 0}

    def service() -> str:
        called["count"] += 1
        return "called"

    denied = _request(enabled=True, verified_at=timezone.now())
    denied.permission_codes = frozenset()
    with pytest.raises(PermissionDenied):
        call_authorized_service(request=denied, view=APIView(), tool=tool, service=service)

    missing_feature = _request(enabled=True, verified_at=timezone.now())
    missing_feature.gym = None
    with pytest.raises(PermissionDenied):
        call_authorized_service(
            request=missing_feature,
            view=APIView(),
            tool=entitled,
            service=service,
        )

    missing_session = _request(enabled=True, verified_at=timezone.now())
    missing_session.refresh_session = None
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        call_authorized_service(
            request=missing_session,
            view=APIView(),
            tool=tool,
            service=service,
        )
    assert called["count"] == 0


def test_failed_mfa_does_not_call_the_service() -> None:
    tool = ToolDefinition(
        name="revoke_owner",
        description="Revoke an owner",
        module="owners",
        operation="write",
        permissions=("staff.manage",),
        requires_recent_mfa=True,
    )
    called = {"count": 0}

    def service() -> str:
        called["count"] += 1
        return "called"

    request = _request(enabled=True, verified_at=timezone.now() - timedelta(hours=2))
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        call_authorized_service(request=request, view=APIView(), tool=tool, service=service)
    assert called["count"] == 0

    fresh = _request(enabled=True, verified_at=timezone.now())
    with override_settings(MFA_ENFORCED=True):
        assert (
            call_authorized_service(request=fresh, view=APIView(), tool=tool, service=service)
            == "called"
        )
    assert called["count"] == 1

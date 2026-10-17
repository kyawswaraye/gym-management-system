from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core import signing
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from apps.audit.serializers import (
    TenantAuditItemSerializer,
    TenantAuditMetaSerializer,
    TenantAuditQuerySerializer,
)
from apps.audit.services import (
    TENANT_AUDIT_CURSOR_SALT,
    TENANT_AUDIT_ONLY_FIELDS,
    TENANT_AUDIT_SCOPE_GYM,
    TENANT_AUDIT_SCOPE_SELF,
    decode_tenant_audit_cursor,
    encode_tenant_audit_cursor,
    tenant_audit_cursor_scope,
    tenant_audit_data,
    tenant_audit_scope,
)
from apps.audit.views import TenantAuditListView
from apps.users.authentication import StaffJWTAuthentication
from apps.users.permissions import RequiresPermission


def _scope(**overrides: object) -> dict[str, str]:
    gym_id = overrides.pop("gym_id", uuid4())
    membership_id = overrides.pop("actor_membership_id", uuid4())
    values: dict[str, object] = {
        "gym_id": gym_id,
        "scope": TENANT_AUDIT_SCOPE_SELF,
        "actor_membership_id": membership_id,
        "action": "",
        "module": "",
        "outcome": "",
        "target_type": "",
        "target_id": None,
    }
    values.update(overrides)
    return tenant_audit_cursor_scope(
        gym_id=values["gym_id"],  # type: ignore[arg-type]
        scope=str(values["scope"]),
        actor_membership_id=values["actor_membership_id"],  # type: ignore[arg-type]
        action=str(values["action"]),
        module=str(values["module"]),
        outcome=str(values["outcome"]),
        target_type=str(values["target_type"]),
        target_id=values["target_id"],  # type: ignore[arg-type]
    )


def test_tenant_audit_view_is_staff_only_and_requires_active_audit_view() -> None:
    assert TenantAuditListView.authentication_classes == [StaffJWTAuthentication]
    assert RequiresPermission in TenantAuditListView.permission_classes
    assert TenantAuditListView.required_permission == "audit.view"


def test_tenant_audit_scope_is_gym_only_for_an_active_owner() -> None:
    assert tenant_audit_scope(is_owner=True, membership_status="ACTIVE") == TENANT_AUDIT_SCOPE_GYM
    assert (
        tenant_audit_scope(is_owner=True, membership_status="SUSPENDED") == TENANT_AUDIT_SCOPE_SELF
    )
    assert tenant_audit_scope(is_owner=False, membership_status="ACTIVE") == TENANT_AUDIT_SCOPE_SELF


def test_tenant_audit_query_rejects_unknown_duplicate_and_unbounded_values() -> None:
    unknown = TenantAuditQuerySerializer(
        data=QueryDict("gym_id=00000000-0000-4000-8000-000000000001")
    )
    duplicate = TenantAuditQuerySerializer(data=QueryDict("action=CREATED&action=UPDATED"))
    both = TenantAuditQuerySerializer(data=QueryDict("gym_id=abc&gym_id=def&limit=10"))
    long_action = TenantAuditQuerySerializer(data={"action": "A" * 101})
    long_module = TenantAuditQuerySerializer(data={"module": "M" * 51})
    long_target = TenantAuditQuerySerializer(data={"target_type": "T" * 101})
    long_cursor = TenantAuditQuerySerializer(data={"after": "x" * 2049})
    bad_outcome = TenantAuditQuerySerializer(data={"outcome": "MAYBE"})
    bad_target = TenantAuditQuerySerializer(data={"target_id": "not-a-uuid"})
    low_limit = TenantAuditQuerySerializer(data={"limit": 0})
    high_limit = TenantAuditQuerySerializer(data={"limit": 101})
    blank = TenantAuditQuerySerializer(data={"action": " "})
    accepted = TenantAuditQuerySerializer(
        data={
            "action": "ROLE_CREATED",
            "module": "roles",
            "outcome": "DENIED",
            "target_type": "Role",
            "target_id": "00000000-0000-4000-8000-000000000009",
            "limit": 100,
            "after": "opaque",
        }
    )
    default_limit = TenantAuditQuerySerializer(data={})

    assert not unknown.is_valid()
    assert unknown.errors["gym_id"] == ["This field is not allowed."]
    assert not duplicate.is_valid()
    assert duplicate.errors["action"] == ["Duplicate value."]
    assert not both.is_valid()
    assert "This field is not allowed." in both.errors["gym_id"]
    assert "Duplicate value." in both.errors["gym_id"]
    assert not long_action.is_valid()
    assert not long_module.is_valid()
    assert not long_target.is_valid()
    assert not long_cursor.is_valid()
    assert not bad_outcome.is_valid()
    assert not bad_target.is_valid()
    assert not low_limit.is_valid()
    assert not high_limit.is_valid()
    assert not blank.is_valid()
    assert accepted.is_valid(), accepted.errors
    assert default_limit.is_valid(), default_limit.errors
    assert default_limit.validated_data["limit"] == 25
    assert set(TenantAuditQuerySerializer().fields) == {
        "action",
        "module",
        "outcome",
        "target_type",
        "target_id",
        "limit",
        "after",
    }


def test_tenant_audit_cursor_is_bound_to_gym_scope_membership_and_filters() -> None:
    membership_id = uuid4()
    gym_id = uuid4()
    target_id = uuid4()
    scope = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    occurred_at = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    audit_id = uuid4()
    issued_at = 1_700_000_000
    with patch("django.core.signing.time.time", return_value=issued_at):
        token = encode_tenant_audit_cursor(
            occurred_at=occurred_at,
            audit_id=audit_id,
            scope=scope,
        )
    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        decoded_at, decoded_id = decode_tenant_audit_cursor(token, scope=scope)
    assert decoded_at == occurred_at
    assert decoded_id == audit_id

    other_membership = _scope(
        gym_id=gym_id,
        actor_membership_id=uuid4(),
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    gym_scope = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_GYM,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    other_gym = _scope(
        gym_id=uuid4(),
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    other_action = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_UPDATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    other_module = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="staff",
        outcome="SUCCESS",
        target_type="Role",
        target_id=target_id,
    )
    other_outcome = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="DENIED",
        target_type="Role",
        target_id=target_id,
    )
    other_target_type = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="StaffProfile",
        target_id=target_id,
    )
    other_target = _scope(
        gym_id=gym_id,
        actor_membership_id=membership_id,
        scope=TENANT_AUDIT_SCOPE_SELF,
        action="ROLE_CREATED",
        module="roles",
        outcome="SUCCESS",
        target_type="Role",
        target_id=uuid4(),
    )
    owner_a = _scope(
        gym_id=gym_id,
        actor_membership_id=uuid4(),
        scope=TENANT_AUDIT_SCOPE_GYM,
        action="",
        module="",
        outcome="",
        target_type="",
        target_id=None,
    )
    owner_b = _scope(
        gym_id=gym_id,
        actor_membership_id=uuid4(),
        scope=TENANT_AUDIT_SCOPE_GYM,
        action="",
        module="",
        outcome="",
        target_type="",
        target_id=None,
    )
    assert owner_a == owner_b
    assert owner_a["actor_membership_id"] == ""

    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        for changed in (
            other_membership,
            gym_scope,
            other_gym,
            other_action,
            other_module,
            other_outcome,
            other_target_type,
            other_target,
        ):
            try:
                decode_tenant_audit_cursor(token, scope=changed)
            except ValidationError as exc:
                assert exc.detail == {"after": ["Invalid cursor."]}
            else:
                raise AssertionError("substituted cursor was accepted")
        tampered = f"{token[:-1]}A" if not token.endswith("A") else f"{token[:-1]}B"
        foreign = signing.dumps(
            {"occurred_at": occurred_at.isoformat(), "id": str(audit_id), **scope},
            salt="platform-audit-list",
            compress=True,
        )
        extra = signing.dumps(
            {
                "occurred_at": occurred_at.isoformat(),
                "id": str(audit_id),
                "unbound": "1",
                **scope,
            },
            salt=TENANT_AUDIT_CURSOR_SALT,
            compress=True,
        )
        for bad in (tampered, foreign, extra):
            try:
                decode_tenant_audit_cursor(bad, scope=scope)
            except ValidationError as exc:
                assert exc.detail == {"after": ["Invalid cursor."]}
            else:
                raise AssertionError("unbound cursor was accepted")
    with patch(
        "django.core.signing.time.time",
        return_value=issued_at + 24 * 60 * 60 + 5,
    ):
        try:
            decode_tenant_audit_cursor(token, scope=scope)
        except ValidationError as exc:
            assert exc.detail == {"after": ["Invalid cursor."]}
        else:
            raise AssertionError("expired cursor was accepted")


def test_tenant_audit_serialization_redacts_nested_credentials_and_omits_sensitive_fields() -> None:
    row = SimpleNamespace(
        id=uuid4(),
        actor_membership_id=None,
        impersonator_membership_id=uuid4(),
        action="LEGACY_ROW",
        module="audit",
        target_type="Gym",
        target_id=uuid4(),
        old_values={
            "password": "raw-password-value",
            "nested": {
                "client_secret": "raw-secret-value",
                "apiCredential": "raw-credential-value",
                "refresh_token": "raw-token-value",
                "status": "ACTIVE",
            },
            "history": [{"access_token": "raw-list-token"}],
            "note": "password reset completed",
        },
        new_values={"credential": "raw-credential-value"},
        request_id="req_legacy",
        outcome="SUCCESS",
        occurred_at=datetime(2026, 10, 17, 9, 0, tzinfo=UTC),
        gym_id=uuid4(),
        actor_user_id=uuid4(),
        ip_address="198.51.100.23",
        user_agent="LegacyAuditBrowser/9",
        created_at=datetime(2026, 10, 17, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 10, 17, 9, 2, tzinfo=UTC),
    )

    data = tenant_audit_data(row)  # type: ignore[arg-type]
    rendered = str(data)

    assert set(data) == set(TENANT_AUDIT_ONLY_FIELDS)
    assert set(TenantAuditItemSerializer().fields) == set(TENANT_AUDIT_ONLY_FIELDS)
    assert set(TenantAuditMetaSerializer().fields) == {"has_more", "next_after", "scope"}
    assert data["old_values"] == {
        "password": "[REDACTED]",
        "nested": {
            "client_secret": "[REDACTED]",
            "apiCredential": "[REDACTED]",
            "refresh_token": "[REDACTED]",
            "status": "ACTIVE",
        },
        "history": [{"access_token": "[REDACTED]"}],
        "note": "password reset completed",
    }
    assert data["new_values"] == {"credential": "[REDACTED]"}
    assert data["actor_membership_id"] is None
    for secret in (
        "raw-password-value",
        "raw-secret-value",
        "raw-credential-value",
        "raw-token-value",
        "raw-list-token",
        "198.51.100.23",
        "LegacyAuditBrowser/9",
        str(row.gym_id),
        str(row.actor_user_id),
    ):
        assert secret not in rendered

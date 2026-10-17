from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer

PUBLISHED_PATHS = {
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
    "/api/v1/auth/gyms/",
    "/api/v1/auth/login/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/logout-all/",
    "/api/v1/auth/me/",
    "/api/v1/auth/mfa/challenge/",
    "/api/v1/auth/mfa/enroll/",
    "/api/v1/auth/mfa/enroll/confirm/",
    "/api/v1/auth/password/forgot/",
    "/api/v1/auth/password/reset/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/select-gym/",
    "/api/v1/gym/",
    "/api/v1/gym-memberships/{id}/roles/",
    "/api/v1/members/",
    "/api/v1/members/{id}/",
    "/api/v1/members/{id}/archive/",
    "/api/v1/members/{id}/summary/",
    "/api/v1/membership-plans/",
    "/api/v1/membership-plans/{id}/",
    "/api/v1/membership-plans/{id}/archive/",
    "/api/v1/memberships/",
    "/api/v1/memberships/expiry/",
    "/api/v1/memberships/{id}/",
    "/api/v1/memberships/{id}/cancel/",
    "/api/v1/memberships/{id}/resume/",
    "/api/v1/memberships/{id}/suspend/",
    "/api/v1/health/",
    "/api/v1/permissions/",
    "/api/v1/readiness/",
    "/api/v1/owners/",
    "/api/v1/settings/",
    "/api/v1/owners/invitations/",
    "/api/v1/owners/invitations/accept/",
    "/api/v1/owners/{gym_membership_id}/revoke/",
    "/api/v1/roles/",
    "/api/v1/roles/{id}/",
    "/api/v1/roles/{id}/archive/",
    "/api/v1/roles/{id}/permissions/",
    "/api/v1/staff/",
    "/api/v1/staff/invitations/",
    "/api/v1/staff/invitations/accept/",
    "/api/v1/staff/{id}/",
    "/api/v1/staff/{id}/activate/",
    "/api/v1/staff/{id}/suspend/",
}
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.v1.json"


def generated_schema() -> dict[str, Any]:
    schema = SchemaGenerator().get_schema(request=None, public=True)
    return json.loads(OpenApiJsonRenderer().render(schema))


def test_committed_openapi_contract_has_no_drift() -> None:
    generated = generated_schema()
    committed = json.loads(CONTRACT_PATH.read_text())
    assert generated == committed


def test_openapi_has_no_retired_platform_settings_route() -> None:
    document = generated_schema()
    rendered = json.dumps(document)
    assert "platform.settings.manage" not in rendered
    assert "/api/v1/admin/settings/" not in document["paths"]
    assert "/api/v1/platform/settings/" not in document["paths"]
    assert not any("platform" in path and "settings" in path for path in document["paths"])


def test_openapi_contract_publishes_approved_routes_only() -> None:
    schema = generated_schema()
    assert set(schema["paths"]) == PUBLISHED_PATHS
    assert "staffBearerAuth" in schema["components"]["securitySchemes"]
    assert "staffOrPlatformBearerAuth" in schema["components"]["securitySchemes"]
    assert "/api/v1/members/" in schema["paths"]
    assert "/api/v1/members/{id}/" in schema["paths"]
    assert "/api/v1/members/{id}/archive/" in schema["paths"]
    unpublished = (
        "/api/v1/members/{id}/payments/",
        "/api/v1/members/{id}/check-ins/",
        "/api/v1/notifications/",
        "/api/v1/payments/",
        "/api/v1/receipts/",
    )
    assert "/api/v1/members/{id}/summary/" in schema["paths"]
    assert all(path not in schema["paths"] for path in unpublished)
    assert not any(path.startswith(unpublished) for path in schema["paths"])
    assert not any("check-ins" in path for path in schema["paths"])
    assert "/api/v1/payments/" not in schema["paths"]
    assert "/api/v1/receipts/" not in schema["paths"]
    assert "/api/v1/dashboard/" not in schema["paths"]


def test_openapi_documents_membership_plans() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/membership-plans/"]
    detail = document["paths"]["/api/v1/membership-plans/{id}/"]
    archive = document["paths"]["/api/v1/membership-plans/{id}/archive/"]["post"]
    schemas = document["components"]["schemas"]
    create = schemas["MembershipPlanCreateRequest"]["properties"]
    update = schemas["PatchedMembershipPlanUpdateRequest"]["properties"]
    plan = schemas["MembershipPlan"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    meta = schemas["MembershipPlanListEnvelope"]["properties"]["meta"]
    meta_schema = schemas[meta["$ref"].rsplit("/", 1)[-1]] if "$ref" in meta else meta
    writable = {"code", "name", "description", "duration_value", "duration_unit", "price"}

    assert set(create) == writable
    assert set(update) == writable
    assert set(plan) == writable | {
        "id",
        "currency",
        "is_active",
        "archived_at",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in plan
    assert set(query) == {"after", "limit"}
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["minimum"] == 1
    assert query["limit"]["schema"]["maximum"] == 100
    assert set(meta_schema["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        archive,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert (
        archive.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        == {}
    )


def test_openapi_documents_memberships() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/memberships/"]
    detail = document["paths"]["/api/v1/memberships/{id}/"]
    cancel = document["paths"]["/api/v1/memberships/{id}/cancel/"]["post"]
    suspend = document["paths"]["/api/v1/memberships/{id}/suspend/"]["post"]
    resume = document["paths"]["/api/v1/memberships/{id}/resume/"]["post"]
    expiry = document["paths"]["/api/v1/memberships/expiry/"]["get"]
    schemas = document["components"]["schemas"]
    create = schemas["MembershipCreateRequest"]["properties"]
    update = schemas["PatchedMembershipUpdateRequest"]["properties"]
    cancel_body = schemas["MembershipCancelRequest"]["properties"]
    membership = schemas["Membership"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    expiry_query = {parameter["name"]: parameter for parameter in expiry["parameters"]}
    meta = _resolve_schema(document, schemas["MembershipListEnvelope"]["properties"]["meta"])
    writable = {"member_id", "plan_id", "start_date", "discount_amount", "note"}

    assert set(create) == writable
    assert set(update) == {"note"}
    assert set(cancel_body) == {"reason"}
    assert set(membership) == writable | {
        "id",
        "plan_code",
        "plan_name",
        "duration_value",
        "duration_unit",
        "expiry_date",
        "list_price",
        "final_amount",
        "currency",
        "payment_status",
        "status",
        "cancelled_at",
        "cancelled_by_membership_id",
        "cancellation_reason",
        "created_by_membership_id",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in membership
    assert "revenue_source" not in membership
    assert "paid_amount" not in membership
    assert set(query) == {"after", "limit", "member_id", "payment_status", "status"}
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["maximum"] == 100
    assert set(expiry_query) == {"after", "bucket", "limit"}
    bucket = expiry_query["bucket"]["schema"]
    assert set(bucket["enum"]) == {
        "expired",
        "today",
        "within_3_days",
        "within_7_days",
        "within_30_days",
    }
    assert set(meta["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        cancel,
        suspend,
        resume,
        expiry,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in collection["get"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert "409" in cancel["responses"]
    assert (
        suspend.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        == {}
    )
    assert "{action}" not in "".join(document["paths"])


def _resolve_schema(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_openapi_documents_members() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/members/"]
    detail = document["paths"]["/api/v1/members/{id}/"]
    archive = document["paths"]["/api/v1/members/{id}/archive/"]["post"]
    schemas = document["components"]["schemas"]
    create = schemas["MemberCreateRequest"]["properties"]
    update = schemas["PatchedMemberUpdateRequest"]["properties"]
    member = schemas["Member"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    meta = _resolve_schema(document, schemas["MemberListEnvelope"]["properties"]["meta"])
    address = _resolve_schema(document, member["address"])
    gender = schemas["GenderEnum"]
    writable = {
        "member_number",
        "first_name",
        "last_name",
        "phone",
        "email",
        "gender",
        "date_of_birth",
        "address",
        "join_date",
        "emergency_contact_name",
        "emergency_contact_phone",
        "notes",
        "profile_photo_asset_id",
    }

    assert set(create) == writable
    assert set(update) == writable - {"member_number"}
    assert set(member) == writable | {
        "id",
        "normalized_full_name",
        "linked_user_id",
        "is_pt_client",
        "authz_version",
        "status",
        "archived_at",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in member
    assert "created_by_membership_id" not in member
    assert "updated_by_membership_id" not in member
    assert member["is_pt_client"]["type"] == "boolean"
    assert member["phone"]["nullable"] is True
    assert member["linked_user_id"]["nullable"] is True
    assert set(address["properties"]) == {
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
    }
    assert set(gender["enum"]) == {"FEMALE", "MALE", "OTHER", "UNSPECIFIED"}
    assert set(query) == {
        "after",
        "is_pt_client",
        "joined_from",
        "joined_to",
        "limit",
        "search",
        "status",
    }
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["minimum"] == 1
    assert query["limit"]["schema"]["maximum"] == 100
    assert query["search"]["schema"]["maxLength"] == 100
    assert set(meta["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        archive,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert "404" in archive["responses"]
    assert (
        archive.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        == {}
    )
    rendered_members = json.dumps(
        {
            "collection": collection,
            "detail": detail,
            "archive": archive,
            "member": member,
        }
    )
    assert "object_key" not in rendered_members


def _schema_component(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    for item in schema.get("allOf", []):
        if isinstance(item, dict) and "$ref" in item:
            return document["components"]["schemas"][item["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_openapi_documents_member_summary() -> None:
    document = generated_schema()
    operation = document["paths"]["/api/v1/members/{id}/summary/"]
    assert set(operation) == {"get"}
    summary = operation["get"]
    parameters = summary.get("parameters", [])
    assert [parameter["name"] for parameter in parameters] == ["id"]
    assert parameters[0]["in"] == "path"
    assert not any(parameter["in"] == "query" for parameter in parameters)
    assert {"staffBearerAuth": []} in summary["security"]
    assert set(summary["responses"]) >= {"200", "400", "401", "403", "404"}
    schemas = document["components"]["schemas"]
    envelope = schemas["MemberSummaryEnvelope"]["properties"]
    assert set(envelope) == {"success", "data", "message"}
    data = _schema_component(document, envelope["data"])
    assert set(data["properties"]) == {
        "member",
        "current_membership",
        "days_remaining",
        "is_pt_client",
        "membership_history",
        "membership_history_has_more",
        "payment_summary",
        "check_in_summary",
    }
    assert data["properties"]["current_membership"].get("nullable") is True
    assert data["properties"]["days_remaining"]["nullable"] is True
    assert data["properties"]["days_remaining"]["minimum"] == 0
    assert data["properties"]["is_pt_client"]["type"] == "boolean"
    member = _schema_component(document, data["properties"]["member"])
    assert set(member["properties"]) == set(schemas["Member"]["properties"])
    current = _schema_component(document, data["properties"]["current_membership"])
    assert set(current["properties"]) == set(schemas["Membership"]["properties"])
    history_item = _schema_component(document, data["properties"]["membership_history"]["items"])
    assert set(history_item["properties"]) == set(schemas["Membership"]["properties"])
    payment = _schema_component(document, data["properties"]["payment_summary"])
    assert set(payment["properties"]) == {
        "available",
        "currency",
        "total_charged",
        "total_refunded",
        "net_paid",
        "outstanding_membership_balance",
    }
    for name in ("total_charged", "total_refunded", "net_paid", "outstanding_membership_balance"):
        assert payment["properties"][name]["type"] == "string"
        assert payment["properties"][name]["format"] == "decimal"
    check_in = _schema_component(document, data["properties"]["check_in_summary"])
    assert set(check_in["properties"]) == {"available"}
    rendered = json.dumps(payment)
    for secret in (
        "receipt",
        "reference",
        "description",
        "method",
        "entry_type",
        "received_at",
        "business_date",
        "approved_by",
        "actor",
    ):
        assert secret not in rendered
    assert "payment_history" not in data["properties"]
    assert "check_in_history" not in data["properties"]


def test_openapi_documents_bff_staff_and_platform_authentication_boundaries() -> None:
    document = generated_schema()
    login = document["paths"]["/api/v1/auth/login/"]["post"]
    me = document["paths"]["/api/v1/auth/me/"]["get"]

    assert any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in login["parameters"])
    assert login.get("security", []) == []
    assert {"staffOrPlatformBearerAuth": []} in me["security"]
    assert document["components"]["securitySchemes"]["staffBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert document["components"]["securitySchemes"]["staffOrPlatformBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }


def test_openapi_documents_platform_provisioning_idempotency() -> None:
    document = generated_schema()
    create = document["paths"]["/api/v1/admin/gyms/"]["post"]
    header = next(
        parameter for parameter in create["parameters"] if parameter["name"] == "Idempotency-Key"
    )

    assert header["required"] is True
    assert {"platformBearerAuth": []} in create["security"]
    assert "201" in create["responses"]
    assert "409" in create["responses"]
    assert "422" in create["responses"]
    owner_input = document["components"]["schemas"]["AdminOwnerInputRequest"]["properties"]
    assert owner_input["send_invitation"]["default"] is True


def test_openapi_documents_tenant_audit_reads() -> None:
    document = generated_schema()
    audit = document["paths"]["/api/v1/audit-logs/"]["get"]
    schemas = document["components"]["schemas"]
    parameters = {parameter["name"]: parameter for parameter in audit["parameters"]}
    item = schemas["TenantAuditItem"]["properties"]
    meta = schemas["TenantAuditMeta"]["properties"]
    scope = meta["scope"]
    if "$ref" in scope:
        scope = schemas[scope["$ref"].rsplit("/", 1)[-1]]
    outcome = parameters["outcome"]["schema"]
    if "$ref" in outcome:
        outcome = schemas[outcome["$ref"].rsplit("/", 1)[-1]]

    assert {"staffBearerAuth": []} in audit["security"]
    assert audit["security"] == [{"staffBearerAuth": []}]
    assert set(parameters) == {
        "action",
        "after",
        "limit",
        "module",
        "outcome",
        "target_id",
        "target_type",
    }
    assert parameters["limit"]["schema"]["default"] == 25
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["action"]["schema"]["maxLength"] == 100
    assert parameters["module"]["schema"]["maxLength"] == 50
    assert parameters["target_type"]["schema"]["maxLength"] == 100
    assert parameters["after"]["schema"]["maxLength"] == 2048
    assert parameters["target_id"]["schema"]["format"] == "uuid"
    assert set(outcome["enum"]) == {"SUCCESS", "DENIED", "ERROR"}
    assert set(item) == {
        "id",
        "actor_membership_id",
        "impersonator_membership_id",
        "action",
        "module",
        "target_type",
        "target_id",
        "old_values",
        "new_values",
        "request_id",
        "outcome",
        "occurred_at",
    }
    assert item["actor_membership_id"]["nullable"] is True
    assert item["impersonator_membership_id"]["nullable"] is True
    assert "gym_id" not in item
    assert "actor_email" not in item
    assert "ip_address" not in item
    assert "user_agent" not in item
    assert set(meta) == {"has_more", "next_after", "scope"}
    assert set(scope["enum"]) == {"GYM", "SELF"}
    assert "200" in audit["responses"]
    assert "400" in audit["responses"]
    assert "401" in audit["responses"]
    assert "403" in audit["responses"]


def test_openapi_documents_gym_lifecycle_and_platform_audit_security() -> None:
    document = generated_schema()
    detail = document["paths"]["/api/v1/admin/gyms/{gym_id}/"]
    activate = document["paths"]["/api/v1/admin/gyms/{gym_id}/activate/"]["post"]
    suspend = document["paths"]["/api/v1/admin/gyms/{gym_id}/suspend/"]["post"]
    audit = document["paths"]["/api/v1/admin/audit-logs/"]["get"]

    assert {"platformBearerAuth": []} in detail["get"]["security"]
    assert {"platformBearerAuth": []} in detail["patch"]["security"]
    assert {"platformBearerAuth": []} in activate["security"]
    assert {"platformBearerAuth": []} in suspend["security"]
    assert {"platformBearerAuth": []} in audit["security"]
    assert "422" in activate["responses"]
    assert "422" in suspend["responses"]


def test_openapi_documents_staff_and_roles() -> None:
    document = generated_schema()
    invite = document["paths"]["/api/v1/staff/invitations/"]["post"]
    accept = document["paths"]["/api/v1/staff/invitations/accept/"]["post"]
    update = document["paths"]["/api/v1/staff/{id}/"]["patch"]
    roles = document["paths"]["/api/v1/roles/"]
    detail = document["paths"]["/api/v1/roles/{id}/"]
    archive = document["paths"]["/api/v1/roles/{id}/archive/"]["post"]
    permissions = document["paths"]["/api/v1/permissions/"]["get"]
    replace = document["paths"]["/api/v1/roles/{id}/permissions/"]["put"]
    membership = document["paths"]["/api/v1/gym-memberships/{id}/roles/"]
    assert {"staffBearerAuth": []} in update["security"]
    schemas = document["components"]["schemas"]
    invite_properties = schemas["StaffInviteInputRequest"]["properties"]
    update_properties = schemas["PatchedStaffUpdateRequest"]["properties"]

    assert {"staffBearerAuth": []} in invite["security"]
    assert {"staffBearerAuth": []} in roles["get"]["security"]
    assert {"staffBearerAuth": []} in roles["post"]["security"]
    assert {"staffBearerAuth": []} in detail["get"]["security"]
    assert {"staffBearerAuth": []} in detail["patch"]["security"]
    assert {"staffBearerAuth": []} in archive["security"]
    assert {"staffBearerAuth": []} in permissions["security"]
    assert {"staffBearerAuth": []} in replace["security"]
    assert {"staffBearerAuth": []} in membership["get"]["security"]
    assert {"staffBearerAuth": []} in membership["put"]["security"]
    assert accept.get("security", []) == []
    assert any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in accept["parameters"])
    assert "role_ids" in invite_properties
    assert set(invite_properties).isdisjoint(
        {
            "gym_id",
            "is_owner",
            "status",
            "authz_version",
            "invitation_token",
            "profile_photo_asset_id",
        }
    )
    assert set(update_properties) == {
        "staff_code",
        "display_name",
        "phone",
        "joined_date",
        "employment_title",
        "default_commission_percent",
    }
    assert "put" not in document["paths"]["/api/v1/staff/{id}/"]
    assert "RoleChoice" not in schemas
    assert "RoleChoiceListEnvelope" not in schemas
    staff_list = document["paths"]["/api/v1/staff/"]["get"]
    parameters = {parameter["name"]: parameter for parameter in staff_list["parameters"]}
    assert set(parameters) == {"after", "limit", "membership_status", "search", "status"}
    assert parameters["limit"]["schema"]["default"] == 25
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    meta = schemas["StaffListEnvelope"]["properties"]["meta"]
    meta_schema = schemas[meta["$ref"].rsplit("/", 1)[-1]] if "$ref" in meta else meta
    assert set(meta_schema["properties"]) == {"has_more", "next_after"}

    for operation in (roles["get"], permissions):
        query = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert set(query) == {"after", "limit"}
        assert query["limit"]["schema"]["default"] == 25
        assert query["limit"]["schema"]["minimum"] == 1
        assert query["limit"]["schema"]["maximum"] == 100

    create_properties = schemas["RoleCreateRequest"]["properties"]
    update_role_properties = schemas["PatchedRoleUpdateRequest"]["properties"]
    permission_properties = schemas["RolePermissionReplaceRequest"]["properties"]
    membership_properties = schemas["MembershipRoleReplaceRequest"]["properties"]
    role_properties = schemas["Role"]["properties"]
    assert set(create_properties) == {"key", "name", "description", "is_active"}
    assert set(update_role_properties) == {"key", "name", "description", "is_active"}
    assert set(permission_properties) == {"permission_codes"}
    assert set(membership_properties) == {"role_ids"}
    assert {
        "is_protected",
        "is_template",
        "permission_codes",
        "permission_count",
        "assignment_count",
    } <= set(role_properties)
    assert set(schemas["MembershipRole"]["properties"]) == {
        "gym_membership_id",
        "roles",
        "authz_version",
    }
    assert "post" not in document["paths"]["/api/v1/roles/{id}/"]
    assert "get" not in document["paths"]["/api/v1/roles/{id}/archive/"]
    assert "post" not in document["paths"]["/api/v1/roles/{id}/permissions/"]


def test_openapi_documents_tenant_gym_profile_and_settings() -> None:
    document = generated_schema()
    profile = document["paths"]["/api/v1/gym/"]
    settings = document["paths"]["/api/v1/settings/"]
    schemas = document["components"]["schemas"]

    assert {"staffBearerAuth": []} in profile["get"]["security"]
    assert {"staffBearerAuth": []} in profile["patch"]["security"]
    assert {"staffBearerAuth": []} in settings["get"]["security"]
    assert {"staffBearerAuth": []} in settings["patch"]["security"]
    assert "400" in profile["patch"]["responses"]
    assert "400" in settings["patch"]["responses"]

    profile_read = schemas["GymProfile"]["properties"]
    assert {
        "id",
        "code",
        "name",
        "legal_name",
        "status",
        "timezone",
        "currency",
        "subscription_plan_id",
        "subscription_plan_name",
        "subscription_status",
        "subscription_starts_at",
        "subscription_ends_at",
        "created_at",
        "updated_at",
    } <= set(profile_read)
    assert profile_read["subscription_plan_id"]["nullable"] is True
    assert profile_read["subscription_plan_name"]["nullable"] is True
    assert profile_read["subscription_starts_at"]["nullable"] is True
    assert profile_read["subscription_ends_at"]["nullable"] is True

    profile_write = schemas["PatchedGymProfileUpdateRequest"]["properties"]
    assert {
        "name",
        "legal_name",
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
        "timezone",
        "currency",
    } <= set(profile_write)
    assert set(profile_write).isdisjoint(
        {
            "id",
            "code",
            "status",
            "gym_id",
            "subscription_plan_id",
            "subscription_plan_name",
            "subscription_status",
            "subscription_starts_at",
            "subscription_ends_at",
            "created_at",
            "updated_at",
        }
    )

    settings_write = schemas["PatchedGymSettingsUpdateRequest"]["properties"]
    assert "require_separate_finance_approval" in settings_write
    assert set(settings_write).isdisjoint(
        {
            "gym_id",
            "next_member_number",
            "created_by_membership_id",
            "updated_by_membership_id",
            "created_at",
            "updated_at",
        }
    )
    settings_read = schemas["GymSettings"]["properties"]
    assert "next_member_number" in settings_read
    assert "require_separate_finance_approval" in settings_read


def test_openapi_documents_platform_dashboard_and_subscription_update() -> None:
    document = generated_schema()
    dashboard = document["paths"]["/api/v1/admin/dashboard/"]["get"]
    subscriptions = document["paths"]["/api/v1/admin/subscriptions/"]["get"]
    update = document["paths"]["/api/v1/admin/subscriptions/{id}/"]["patch"]
    schemas = document["components"]["schemas"]

    assert {"platformBearerAuth": []} in dashboard["security"]
    assert {"platformBearerAuth": []} in subscriptions["security"]
    assert {"platformBearerAuth": []} in update["security"]
    assert "409" in update["responses"]
    assert "404" in update["responses"]
    assert "patch" not in document["paths"]["/api/v1/admin/subscriptions/"]
    assert "get" not in document["paths"]["/api/v1/admin/subscriptions/{id}/"]

    dashboard_properties = schemas["AdminDashboard"]["properties"]
    assert set(dashboard_properties) == {
        "gym_statuses",
        "gym_subscription_statuses",
        "subscription_plan_statuses",
    }
    assert set(schemas["GymStatusCounts"]["properties"]) == {
        "total",
        "PENDING",
        "ACTIVE",
        "SUSPENDED",
        "CLOSED",
    }
    assert set(schemas["GymSubscriptionStatusCounts"]["properties"]) == {
        "TRIAL",
        "ACTIVE",
        "PAST_DUE",
        "CANCELLED",
    }
    assert set(schemas["SubscriptionPlanStatusCounts"]["properties"]) == {"ACTIVE", "RETIRED"}

    parameters = {parameter["name"]: parameter for parameter in subscriptions["parameters"]}
    status_parameter = parameters["status"]["schema"]
    if "$ref" in status_parameter:
        status_parameter = schemas[status_parameter["$ref"].rsplit("/", 1)[-1]]
    list_status = schemas["AdminSubscription"]["properties"]["status"]
    if "$ref" in list_status:
        list_status = schemas[list_status["$ref"].rsplit("/", 1)[-1]]

    assert set(parameters) == {"status"}
    assert parameters["status"].get("required", False) is False
    assert status_parameter["default"] == "ACTIVE"
    assert set(status_parameter["enum"]) == {"ACTIVE", "RETIRED", "ALL"}
    assert "400" in subscriptions["responses"]
    list_properties = schemas["AdminSubscription"]["properties"]
    assert set(list_properties) == {
        "id",
        "code",
        "name",
        "status",
        "price",
        "currency",
        "billing_interval",
    }
    assert set(list_status["enum"]) == {"ACTIVE", "RETIRED"}
    detail_properties = schemas["AdminSubscriptionDetail"]["properties"]
    assert set(detail_properties) == {
        "id",
        "code",
        "name",
        "status",
        "price",
        "currency",
        "billing_interval",
        "created_at",
        "updated_at",
    }
    update_properties = schemas["PatchedAdminSubscriptionUpdateRequest"]["properties"]
    status_schema = schemas[update_properties["status"]["$ref"].rsplit("/", 1)[-1]]
    assert set(update_properties) == {"name", "status", "price", "billing_interval"}
    assert set(status_schema["enum"]) == {"ACTIVE", "RETIRED"}

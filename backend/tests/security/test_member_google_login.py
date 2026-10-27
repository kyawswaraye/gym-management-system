from __future__ import annotations

import os
from uuid import UUID

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import link_member_identity
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User
from tests.security.test_memberships import _member
from tests.security.test_tenant_gym_settings import (
    PASSWORD,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _emailed_member(code: str, email: str) -> tuple[Gym, str]:
    client, token = platform_token(f"platform-{code}@example.test")
    payload = provision(client, token, code=code, owner_email=f"owner-{code}@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    member_id = _member(APIClient(), auth_headers(staff_token(owner)), first_name="Ada")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        member.email = email
        member.save(update_fields=["email"])
    return gym, member_id


def _post(monkeypatch: pytest.MonkeyPatch, email: str) -> Response:
    monkeypatch.setattr("apps.users.member_views.verify_google_id_token", lambda token: email)
    return APIClient().post(
        "/api/v1/member-auth/google/",
        {"id_token": "signed-google-token", "device_name": "pixel"},
        format="json",
    )


@override_settings(**jwt_settings())
def test_google_sign_in_links_an_active_member_without_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = "ada@example.test"
    gym, member_id = _emailed_member("GOOG-OK", email)
    logged_in = _post(monkeypatch, email)
    assert logged_in.status_code == 200, logged_in.content
    data = logged_in.json()["data"]
    assert data["member_id"] == member_id
    assert data["gym_id"] == str(gym.id)
    assert "signed-google-token" not in logged_in.content.decode()
    user = User.objects.get(normalized_email=email)
    assert user.has_usable_password() is False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
    assert member.linked_user_id == user.id
    again = _post(monkeypatch, email)
    assert again.status_code == 200, again.content
    assert User.objects.filter(normalized_email=email).count() == 1


@override_settings(**jwt_settings())
def test_google_sign_in_reports_a_missing_or_inactive_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _post(monkeypatch, "missing@example.test")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "MEMBER_NOT_FOUND"
    assert missing.json()["error"]["message"] == "No member account uses this Google email."

    gym, member_id = _emailed_member("GOOG-OFF", "paused@example.test")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        member.status = Member.Status.SUSPENDED
        member.save(update_fields=["status"])
    inactive = _post(monkeypatch, "paused@example.test")
    assert inactive.status_code == 403
    assert inactive.json()["error"]["code"] == "MEMBER_NOT_ACTIVE"
    assert inactive.json()["error"]["message"] == "This member account is not active."
    assert User.objects.filter(normalized_email="paused@example.test").count() == 0


@override_settings(**jwt_settings())
def test_google_sign_in_does_not_choose_between_two_active_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = "shared@example.test"
    _emailed_member("GOOG-A", email)
    _emailed_member("GOOG-B", email)
    response = _post(monkeypatch, email)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MEMBER_SIGN_IN_AMBIGUOUS"
    assert User.objects.filter(normalized_email=email).count() == 0


@override_settings(**jwt_settings())
def test_google_sign_in_rejects_a_member_linked_to_another_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = "ada-linked@example.test"
    gym, member_id = _emailed_member("GOOG-LINK", email)
    other = User.objects.create_user("someone-else@example.test", PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(member_id), user=other)
    response = _post(monkeypatch, email)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MEMBER_ALREADY_LINKED"

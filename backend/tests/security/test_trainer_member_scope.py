from __future__ import annotations

import os
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.http import Http404
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.files.images import canonicalize
from apps.files.storage import StoredObject
from apps.members.challenge_services import issue_staff_check_in_challenge
from apps.members.checkin_services import check_out_check_in
from apps.members.device_services import page_display_devices
from apps.members.models import Member, Membership
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
    StaffProfile,
)
from apps.trainers.models import TrainerAssignment
from tests.file_fakes import MemoryStore, use_fakes
from tests.security.test_memberships import _enable_memberships, _plan, _sale
from tests.security.test_profile_photo_files import _FAKES, _intent, _png
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _member, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _names(response: object) -> set[str]:
    payload = response.json()  # type: ignore[attr-defined]
    return {row["first_name"] for row in payload["data"]}


def _member_ids(response: object) -> set[str]:
    payload = response.json()  # type: ignore[attr-defined]
    return {str(row["member_id"]) for row in payload["data"]}


def _ordered_member_selects(captured: CaptureQueriesContext) -> list[str]:
    return [
        str(query["sql"]).lower()
        for query in captured.captured_queries
        if "from" in str(query["sql"]).lower()
        and "members_member" in str(query["sql"]).lower()
        and "order by" in str(query["sql"]).lower()
    ]


@_FAKES
@override_settings(**jwt_settings())
def test_trainer_only_member_routes_follow_effective_assignment() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    client, platform_access = platform_token("scope-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="SCP-A", owner_email="scope-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="SCP-B", owner_email="scope-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym_a, email="scope-ada@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym_a, email="scope-bea@example.test", role_key="TRAINER")
    lead_membership = add_staff(gym_a, email="scope-lead@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymMembershipRole.objects.create(
            gym=gym_a,
            gym_membership=lead_membership,
            role=Role.objects.get(gym=gym_a, key="MANAGER"),
        )
    ada = _profile(gym_a, trainer_membership, code="SC-ADA", name="Ada Scope")
    _profile(gym_a, other_trainer, code="SC-BEA", name="Bea Scope")
    _profile(gym_a, lead_membership, code="SC-LEAD", name="Lead Scope")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    bea_headers = auth_headers(staff_token(other_trainer))
    lead_headers = auth_headers(staff_token(lead_membership))
    api = _client()
    today = gym_today(gym_a)
    today_text = today.isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()

    nila = _member(api, owner_headers, first_name="Nila")
    omar = _member(api, owner_headers, first_name="Omar")
    pia = _member(api, owner_headers, first_name="Pia")
    quin = _member(api, owner_headers, first_name="Quin")
    secret = _member(api, owner_headers, first_name="Secret")
    una = _member(api, owner_headers, first_name="Una")
    foreign = _member(api, other_headers, first_name="Sue")
    _enable_memberships(gym_a)
    plan_id = _plan(api, owner_headers, code="SCP-DAY", duration_value=1, duration_unit="DAY")
    nila_membership = _sale(
        api, owner_headers, member_id=nila, plan_id=plan_id, start_date=today_text
    )
    omar_membership = _sale(
        api, owner_headers, member_id=omar, plan_id=plan_id, start_date=today_text
    )
    una_membership = _sale(
        api, owner_headers, member_id=una, plan_id=plan_id, start_date=today_text
    )
    assert nila_membership.status_code == 201, nila_membership.json()
    assert omar_membership.status_code == 201, omar_membership.json()
    assert una_membership.status_code == 201, una_membership.json()
    una_membership_id = una_membership.json()["data"]["id"]
    omar_membership_id = omar_membership.json()["data"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        Member.objects.filter(id=secret).update(status=Member.Status.SUSPENDED)
    owner_omar_check_in = api.post(
        "/api/v1/check-ins/",
        {"member_id": omar},
        format="json",
        HTTP_IDEMPOTENCY_KEY="scope-omar-check-in",
        **owner_headers,
    )
    assert owner_omar_check_in.status_code == 201, owner_omar_check_in.json()
    omar_check_in_id = owner_omar_check_in.json()["data"]["id"]

    before = api.get("/api/v1/members/?limit=25", **trainer_headers)
    assert before.status_code == 200, before.json()
    assert _names(before).isdisjoint({"Nila", "Omar", "Pia", "Quin", "Secret", "Sue", "Una"})
    escalation = _assign(
        api, trainer_headers, trainer_id=str(ada.id), member_id=omar, start=today_text
    )
    assert escalation.status_code == 404
    rendered = escalation.content.decode()
    assert "Omar" not in rendered
    assert "MEMBER_NOT_ACTIVE" not in rendered
    hidden_secret = _assign(
        api, trainer_headers, trainer_id=str(ada.id), member_id=secret, start=today_text
    )
    assert hidden_secret.status_code == 404
    assert "Secret" not in hidden_secret.content.decode()
    assert "MEMBER_NOT_ACTIVE" not in hidden_secret.content.decode()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert TrainerAssignment.objects.filter(member_id__in=[omar, secret]).count() == 0

    for path in (
        f"/api/v1/members/{nila}/",
        f"/api/v1/members/{nila}/summary/",
        f"/api/v1/members/{omar}/check-ins/",
        f"/api/v1/memberships/{omar_membership_id}/",
        f"/api/v1/check-ins/{omar_check_in_id}/",
        f"/api/v1/members/{foreign}/",
    ):
        hidden = api.get(path, **trainer_headers)
        assert hidden.status_code == 404, path
        assert "Nila" not in hidden.content.decode()
        assert "Omar" not in hidden.content.decode()
        assert "Sue" not in hidden.content.decode()
    untouched = api.patch(
        f"/api/v1/members/{nila}/",
        {"notes": "Trainer note"},
        format="json",
        **trainer_headers,
    )
    assert untouched.status_code == 404
    assert "Trainer note" not in untouched.content.decode()
    image = _png()
    hidden_upload = api.post(
        "/api/v1/files/upload-intents/", _intent(omar, image), format="json", **trainer_headers
    )
    assert hidden_upload.status_code == 404
    assert api.get(f"/api/v1/memberships/?member_id={omar}", **trainer_headers).status_code == 404
    assert api.get(f"/api/v1/check-ins/?member_id={omar}", **trainer_headers).status_code == 404
    early_check_in = api.post(
        "/api/v1/check-ins/",
        {"member_id": secret},
        format="json",
        HTTP_IDEMPOTENCY_KEY="scope-secret-check-in",
        **trainer_headers,
    )
    assert early_check_in.status_code == 404
    assert "CHECK_IN_INELIGIBLE" not in early_check_in.content.decode()

    created = _assign(api, owner_headers, trainer_id=str(ada.id), member_id=nila, start=today_text)
    assert created.status_code == 201, created.json()
    assignment_id = created.json()["data"]["id"]
    future = _assign(api, owner_headers, trainer_id=str(ada.id), member_id=pia, start=tomorrow)
    expired = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=quin,
        start=yesterday,
        until=yesterday,
    )
    assert future.status_code == 201, future.json()
    assert expired.status_code == 201, expired.json()

    with CaptureQueriesContext(connection) as trainer_queries:
        assigned = api.get("/api/v1/members/?limit=25", **trainer_headers)
    assert assigned.status_code == 200, assigned.json()
    assert _names(assigned) == {"Nila"}
    trainer_selects = _ordered_member_selects(trainer_queries)
    assert len(trainer_selects) == 1
    assert "trainers_trainer_assignment" in trainer_selects[0]
    assert " join " not in trainer_selects[0]
    with CaptureQueriesContext(connection) as owner_queries:
        everyone = api.get("/api/v1/members/?limit=25", **owner_headers)
    assert {"Nila", "Omar", "Pia", "Quin", "Secret"} <= _names(everyone)
    owner_selects = _ordered_member_selects(owner_queries)
    assert len(owner_selects) == 1
    assert " join " not in owner_selects[0]
    false_page = api.get("/api/v1/members/?is_pt_client=false", **trainer_headers)
    assert "Omar" not in _names(false_page)
    assert api.get(f"/api/v1/members/{nila}/", **trainer_headers).status_code == 200
    summary = api.get(f"/api/v1/members/{nila}/summary/", **trainer_headers)
    assert summary.status_code == 200, summary.json()
    assert summary.json()["data"]["is_pt_client"] is True
    for hidden_id in (omar, pia, quin, foreign):
        hidden_member = api.get(f"/api/v1/members/{hidden_id}/", **trainer_headers)
        hidden_summary = api.get(f"/api/v1/members/{hidden_id}/summary/", **trainer_headers)
        assert hidden_member.status_code == 404
        assert hidden_summary.status_code == 404
    still_closed = api.patch(
        f"/api/v1/members/{nila}/",
        {"notes": "Assigned note"},
        format="json",
        **trainer_headers,
    )
    assert still_closed.status_code == 404
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert Member.objects.get(id=nila).notes == ""

    memberships = api.get("/api/v1/memberships/?limit=25", **trainer_headers)
    assert memberships.status_code == 200, memberships.json()
    assert _member_ids(memberships) == {nila}
    hidden_membership = api.get(f"/api/v1/memberships/{omar_membership_id}/", **trainer_headers)
    assert hidden_membership.status_code == 404
    nila_membership_id = nila_membership.json()["data"]["id"]
    visible_membership = api.get(f"/api/v1/memberships/{nila_membership_id}/", **trainer_headers)
    assert visible_membership.status_code == 200
    expiry = api.get("/api/v1/memberships/expiry/?bucket=today", **trainer_headers)
    assert expiry.status_code == 200, expiry.json()
    assert _member_ids(expiry) == {nila}
    owner_expiry = api.get("/api/v1/memberships/expiry/?bucket=today", **owner_headers)
    assert {nila, omar} <= _member_ids(owner_expiry)

    check_ins = api.get("/api/v1/check-ins/?limit=25", **trainer_headers)
    assert check_ins.status_code == 200, check_ins.json()
    assert check_ins.json()["data"] == []
    denied_visit = api.post(
        "/api/v1/check-ins/",
        {"member_id": omar},
        format="json",
        HTTP_IDEMPOTENCY_KEY="scope-trainer-omar",
        **trainer_headers,
    )
    assert denied_visit.status_code == 404
    nila_visit = api.post(
        "/api/v1/check-ins/",
        {"member_id": nila},
        format="json",
        HTTP_IDEMPOTENCY_KEY="scope-trainer-nila",
        **trainer_headers,
    )
    assert nila_visit.status_code == 201, nila_visit.json()
    nila_check_in_id = nila_visit.json()["data"]["id"]
    visible_visits = api.get("/api/v1/check-ins/?limit=25", **trainer_headers)
    assert [row["member"]["id"] for row in visible_visits.json()["data"]] == [nila]
    assert api.get(f"/api/v1/check-ins/{nila_check_in_id}/", **trainer_headers).status_code == 200
    assert api.get(f"/api/v1/check-ins/{omar_check_in_id}/", **trainer_headers).status_code == 404
    assert api.get(f"/api/v1/members/{nila}/check-ins/", **trainer_headers).status_code == 200
    assert api.get(f"/api/v1/members/{omar}/check-ins/", **trainer_headers).status_code == 404
    checkout = api.post(
        f"/api/v1/check-ins/{nila_check_in_id}/check-out/",
        {},
        format="json",
        **trainer_headers,
    )
    assert checkout.status_code == 403
    assert checkout.json()["error"]["code"] == "PERMISSION_DENIED"

    upload = api.post(
        "/api/v1/files/upload-intents/", _intent(nila, image), format="json", **trainer_headers
    )
    assert upload.status_code == 201, upload.json()
    asset_id = upload.json()["data"]["file_id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        from apps.files.models import FileAsset

        asset = FileAsset.objects.get(gym=gym_a, id=asset_id)
    store.objects[asset.object_key] = StoredObject(body=image, metadata={})
    completed = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **trainer_headers,
    )
    assert completed.status_code == 200, completed.json()
    downloaded = api.get(f"/api/v1/files/{asset.id}/download-url/", **trainer_headers)
    assert downloaded.status_code == 200, downloaded.json()
    deleted = api.delete(f"/api/v1/files/{asset.id}/", **trainer_headers)
    assert deleted.status_code == 403
    assert deleted.json()["error"]["code"] == "PERMISSION_DENIED"
    assert (
        api.post(
            "/api/v1/files/upload-intents/",
            _intent(omar, image),
            format="json",
            **trainer_headers,
        ).status_code
        == 404
    )
    payments = api.get(f"/api/v1/members/{nila}/payments/", **trainer_headers)
    assert payments.status_code == 403
    assert payments.json()["error"]["code"] == "PERMISSION_DENIED"
    challenge = api.post(
        "/api/v1/check-in-challenges/",
        {"display_location": "Floor"},
        format="json",
        **trainer_headers,
    )
    assert challenge.status_code == 403
    assert api.get("/api/v1/check-in-display-devices/", **trainer_headers).status_code == 403

    with pytest.raises(Http404):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            check_out_check_in(
                gym=gym_a,
                actor=trainer_membership,
                check_in_id=nila_check_in_id,
                occurred_at=None,
                request_id="scope-checkout",
                ip_address=None,
                user_agent="scope-test",
            )
    with pytest.raises(Http404):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            issue_staff_check_in_challenge(
                gym=gym_a,
                actor=trainer_membership,
                display_location="Floor",
                request_id="scope-challenge",
                ip_address=None,
                user_agent="scope-test",
            )
    with pytest.raises(Http404):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            page_display_devices(gym=gym_a, actor=trainer_membership, limit=25, after="")

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        StaffProfile.objects.filter(id=ada.id).update(status=StaffProfile.Status.ON_LEAVE)
    assert api.get(f"/api/v1/members/{nila}/", **trainer_headers).status_code == 404
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        StaffProfile.objects.filter(id=ada.id).update(status=StaffProfile.Status.ACTIVE)
    assert api.get(f"/api/v1/members/{nila}/", **trainer_headers).status_code == 200

    hidden_detail = api.get(f"/api/v1/trainer-assignments/{assignment_id}/", **bea_headers)
    assert hidden_detail.status_code == 404
    assert "Nila" not in hidden_detail.content.decode()
    hidden_end = api.post(
        f"/api/v1/trainer-assignments/{assignment_id}/end/",
        {"effective_until": today_text, "reason": "Not my client"},
        format="json",
        **bea_headers,
    )
    assert hidden_end.status_code == 404
    ended = api.post(
        f"/api/v1/trainer-assignments/{assignment_id}/end/",
        {"effective_until": today_text, "reason": "Program finished"},
        format="json",
        **trainer_headers,
    )
    assert ended.status_code == 200, ended.json()
    after_end = api.get("/api/v1/members/?limit=25", **trainer_headers)
    assert "Nila" not in _names(after_end)
    foreign_end = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/cancel/",
        {"reason": "Not mine"},
        format="json",
        **other_headers,
    )
    assert foreign_end.status_code == 404

    lead_list = api.get("/api/v1/members/?limit=25", **lead_headers)
    assert {"Nila", "Omar"} <= _names(lead_list)
    lead_create = _assign(
        api, lead_headers, trainer_id=str(ada.id), member_id=omar, start=today_text
    )
    assert lead_create.status_code == 201, lead_create.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        desk_role = Role.objects.create(
            gym=gym_a,
            key="PTDESK",
            name="PT Desk",
            created_by_membership_id=owner_a.id,
        )
        RolePermission.objects.create(
            gym=gym_a,
            role=desk_role,
            permission=Permission.objects.get(code="pt.assignments.manage"),
            created_by_membership_id=owner_a.id,
        )
    desk = add_staff(gym_a, email="scope-desk@example.test", role_key="PTDESK")
    ivo = _member(api, owner_headers, first_name="Ivo")
    desk_create = _assign(
        api,
        auth_headers(staff_token(desk)),
        trainer_id=str(ada.id),
        member_id=ivo,
        start=today_text,
    )
    assert desk_create.status_code == 201, desk_create.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        floor_role = Role.objects.create(
            gym=gym_a,
            key="FLOOR",
            name="Floor Coach",
            created_by_membership_id=owner_a.id,
        )
        GymMembershipRole.objects.create(
            gym=gym_a,
            gym_membership=trainer_membership,
            role=floor_role,
        )
        trainer_membership.refresh_from_db()
    mixed_headers = auth_headers(staff_token(trainer_membership))
    mixed_list = api.get("/api/v1/members/?limit=25", **mixed_headers)
    assert mixed_list.status_code == 200, mixed_list.json()
    assert {"Omar", "Ivo"} <= _names(mixed_list)
    assert _names(mixed_list).isdisjoint({"Una", "Pia", "Quin", "Secret", "Sue"})
    assert api.get(f"/api/v1/members/{una}/", **mixed_headers).status_code == 404
    mixed_create = _assign(
        api, mixed_headers, trainer_id=str(ada.id), member_id=una, start=today_text
    )
    assert mixed_create.status_code == 404
    assert "Una" not in mixed_create.content.decode()
    assert "MEMBER_NOT_ACTIVE" not in mixed_create.content.decode()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert not TrainerAssignment.objects.filter(member_id=una).exists()
    assert api.get(f"/api/v1/members/{una}/", **lead_headers).status_code == 200

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        RolePermission.objects.create(
            gym=gym_a,
            role=Role.objects.get(gym=gym_a, key="TRAINER"),
            permission=Permission.objects.get(code="memberships.cancel"),
            created_by_membership_id=owner_a.id,
        )
    granted_headers = auth_headers(staff_token(trainer_membership))
    blocked_cancel = api.post(
        f"/api/v1/memberships/{una_membership_id}/cancel/",
        {"reason": "Not assigned"},
        format="json",
        **granted_headers,
    )
    assert blocked_cancel.status_code == 404
    assert "Una" not in blocked_cancel.content.decode()

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        for code in ("members.create", "members.archive", "memberships.create"):
            RolePermission.objects.create(
                gym=gym_a,
                role=floor_role,
                permission=Permission.objects.get(code=code),
                created_by_membership_id=owner_a.id,
            )
        trainer_membership.refresh_from_db()
    command_headers = auth_headers(staff_token(trainer_membership))
    created_member = api.post(
        "/api/v1/members/",
        {"first_name": "Extra", "last_name": "Client", "join_date": "2026-10-01"},
        format="json",
        **command_headers,
    )
    assert created_member.status_code == 404
    assert "Extra" not in created_member.content.decode()
    for member_id in (omar, una):
        archived = api.post(
            f"/api/v1/members/{member_id}/archive/",
            {},
            format="json",
            **command_headers,
        )
        assert archived.status_code == 404
        assert "Omar" not in archived.content.decode()
        assert "Una" not in archived.content.decode()
    denied_sale = _sale(api, command_headers, member_id=una, plan_id=plan_id, start_date=today_text)
    hidden_inactive = _sale(
        api, command_headers, member_id=secret, plan_id=plan_id, start_date=today_text
    )
    assert denied_sale.status_code == 404
    assert hidden_inactive.status_code == 404
    assert "Una" not in denied_sale.content.decode()
    assert "MEMBERSHIP_STATE_CONFLICT" not in hidden_inactive.content.decode()
    assigned_sale = _sale(
        api, command_headers, member_id=ivo, plan_id=plan_id, start_date=today_text
    )
    assert assigned_sale.status_code == 201, assigned_sale.json()
    assert assigned_sale.json()["data"]["member_id"] == ivo
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert not Member.objects.filter(gym=gym_a, first_name="Extra").exists()
        assert Member.objects.get(id=omar).status == Member.Status.ACTIVE
        assert Member.objects.get(id=una).status == Member.Status.ACTIVE
        assert Membership.objects.filter(gym=gym_a, member_id=una).count() == 1


def _enable_finance(gym: Gym) -> None:
    feature, _created = Feature.objects.get_or_create(
        code="finance", defaults={"name": "Finance", "description": "Finance"}
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


@override_settings(**jwt_settings())
def test_trainer_payment_history_stays_closed_before_member_disclosure() -> None:
    client, platform_access = platform_token("pay-scope-platform@example.test")
    payload = provision(
        client, platform_access, code="PAY-S", owner_email="pay-scope-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable_finance(gym)
    trainer_membership = add_staff(gym, email="pay-scope-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="PAY-ADA", name="Ada Pay")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    today = gym_today(gym).isoformat()
    assigned = _member(api, owner_headers, first_name="Nila")
    hidden = _member(api, owner_headers, first_name="Una")
    assert (
        _assign(
            api, owner_headers, trainer_id=str(ada.id), member_id=assigned, start=today
        ).status_code
        == 201
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        desk = Role.objects.create(
            gym=gym,
            key="PAYDESK",
            name="Pay Desk",
            created_by_membership_id=owner.id,
        )
        for code in ("members.view", "payments.view"):
            RolePermission.objects.create(
                gym=gym,
                role=desk,
                permission=Permission.objects.get(code=code),
                created_by_membership_id=owner.id,
            )
        GymMembershipRole.objects.create(gym=gym, gym_membership=trainer_membership, role=desk)
        trainer_membership.refresh_from_db()
    headers = auth_headers(staff_token(trainer_membership))

    def history(member_id: str) -> object:
        with CaptureQueriesContext(connection) as captured:
            response = api.get(f"/api/v1/members/{member_id}/payments/?limit=5", **headers)
        sql = " ".join(query["sql"].lower() for query in captured.captured_queries)
        assert "members_member" not in sql
        assert "members_payment" not in sql
        assert member_id not in sql
        return response

    for member_id, name in ((assigned, "Nila"), (hidden, "Una")):
        denied = history(member_id)
        assert denied.status_code == 404, denied.content
        rendered = denied.content.decode()
        assert name not in rendered
        assert member_id not in rendered
        assert "receipt_number" not in rendered

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer_membership,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
        trainer_membership.refresh_from_db()
    manager_headers = auth_headers(staff_token(trainer_membership))
    opened = api.get(f"/api/v1/members/{assigned}/payments/?limit=5", **manager_headers)
    assert opened.status_code == 200, opened.content
    assert opened.json()["data"] == []
    hidden_opened = api.get(f"/api/v1/members/{hidden}/payments/?limit=5", **manager_headers)
    assert hidden_opened.status_code == 200, hidden_opened.content

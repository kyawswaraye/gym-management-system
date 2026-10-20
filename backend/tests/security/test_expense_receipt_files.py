from __future__ import annotations

import hashlib
import os
from io import BytesIO
from uuid import uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.files.images import canonicalize
from apps.files.models import FileAsset
from apps.files.storage import StoredObject
from apps.members.services import gym_today
from apps.tenants.models import (
    ExpenseCategory,
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    RolePermission,
)
from apps.users.models import User
from apps.users.services import create_platform_session
from tests.file_fakes import MemoryStore, use_fakes
from tests.security.test_profile_photo_files import _intent as _profile_intent
from tests.security.test_profile_photo_files import _member_body
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
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

_FAKES = override_settings(
    **jwt_settings(),
    FILE_OBJECT_STORE="tests.file_fakes.build_store",
    FILE_SCANNER="tests.file_fakes.build_scanner",
    FILE_IMAGE_PROCESSOR="tests.file_fakes.build_processor",
)


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (8, 16, 24)).save(buffer, format="PNG")
    return buffer.getvalue()


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable(gym: Gym, code: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        feature, _created = Feature.objects.get_or_create(code=code, defaults={"name": code})
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


def _gym(code: str) -> tuple[APIClient, Gym, dict[str, str]]:
    admin = User.objects.create_superuser(
        email=f"{code.lower()}-platform@example.test",
        password=PASSWORD,
    )
    token = create_platform_session(user=admin).access_token
    payload = provision(
        _client(),
        token,
        code=code,
        owner_email=f"{code.lower()}-owner@example.test",
    )
    gym_payload = payload["gym"]
    owner_payload = payload["owner"]
    assert isinstance(gym_payload, dict)
    assert isinstance(owner_payload, dict)
    gym = Gym.objects.get(id=gym_payload["id"])
    _enable(gym, "finance")
    _enable(gym, "members")
    return _client(), gym, auth_headers(staff_token(_membership(owner_payload)))


def _membership(owner_payload: dict[str, object]) -> GymMembership:
    membership_payload = owner_payload["gym_membership"]
    assert isinstance(membership_payload, dict)
    return GymMembership.objects.select_related("user").get(id=membership_payload["id"])


def _codes(membership: GymMembership) -> set[str]:
    with transaction.atomic():
        set_gym_context(gym_id=membership.gym_id)
        return set(
            RolePermission.objects.filter(
                gym_id=membership.gym_id,
                role__gymmembershiprole__gym_membership=membership,
                role__is_active=True,
                permission__is_active=True,
            ).values_list("permission__code", flat=True)
        )


def _receipt_body(gym_id: str, image: bytes) -> dict[str, object]:
    return {
        "original_name": "bill.png",
        "content_type": "image/png",
        "byte_size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "purpose": "EXPENSE_RECEIPT",
        "owner_type": "GYM",
        "owner_id": gym_id,
    }


def _asset(gym: Gym, file_id: str) -> FileAsset:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return FileAsset.objects.get(gym=gym, id=file_id)


def _stage(store: MemoryStore, asset: FileAsset, image: bytes) -> None:
    store.objects[asset.object_key] = StoredObject(body=image, metadata={})


def _complete(api: APIClient, headers: dict[str, str], asset: FileAsset, image: bytes):
    return api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )


def _category_id(gym: Gym) -> str:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return str(ExpenseCategory.objects.get(gym_id=gym.id, system_key="INTERNET").id)


@_FAKES
def test_expense_receipt_authorization() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, gym, _owner_headers = _gym(f"RCPT{uuid4().hex[:8].upper()}")
    image = _png()
    accountant = add_staff(gym, email=f"acct-{uuid4().hex[:8]}@example.test", role_key="ACCOUNTANT")
    trainer = add_staff(gym, email=f"trainer-{uuid4().hex[:8]}@example.test", role_key="TRAINER")
    reception = add_staff(
        gym, email=f"desk-{uuid4().hex[:8]}@example.test", role_key="RECEPTIONIST"
    )
    other_reception = add_staff(
        gym, email=f"other-desk-{uuid4().hex[:8]}@example.test", role_key="RECEPTIONIST"
    )
    accountant_codes = _codes(accountant)
    assert "files.upload" in accountant_codes
    assert "files.view" in accountant_codes
    assert "expenses.create" in accountant_codes
    assert "expenses.update" in accountant_codes
    assert "members.update" not in accountant_codes
    assert "files.delete" not in accountant_codes
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    reception_headers = auth_headers(staff_token(reception))
    other_headers = auth_headers(staff_token(other_reception))

    created = api.post(
        "/api/v1/files/upload-intents/",
        _receipt_body(str(gym.id), image),
        format="json",
        **accountant_headers,
    )
    assert created.status_code == 201, created.json()
    own = _asset(gym, created.json()["data"]["file_id"])
    _stage(store, own, image)
    completed = _complete(api, accountant_headers, own, image)
    assert completed.status_code == 200, completed.json()
    downloaded = api.get(f"/api/v1/files/{own.id}/download-url/", **accountant_headers)
    assert downloaded.status_code == 200, downloaded.json()
    assert "expense-receipt.png" in downloaded.json()["data"]["url"]

    missing_intent = api.post(
        "/api/v1/files/upload-intents/",
        _receipt_body(str(gym.id), image),
        format="json",
        **trainer_headers,
    )
    assert missing_intent.status_code == 403
    assert missing_intent.json()["error"]["code"] == "PERMISSION_DENIED"
    for response in (
        _complete(api, trainer_headers, own, image),
        api.get(f"/api/v1/files/{own.id}/download-url/", **trainer_headers),
        api.delete(f"/api/v1/files/{own.id}/", **trainer_headers),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    _other_api, other_gym, _other_owner = _gym(f"RCPX{uuid4().hex[:8].upper()}")
    outsider = add_staff(
        other_gym, email=f"outsider-{uuid4().hex[:8]}@example.test", role_key="ACCOUNTANT"
    )
    outsider_headers = auth_headers(staff_token(outsider))
    for response in (
        _complete(api, outsider_headers, own, image),
        api.get(f"/api/v1/files/{own.id}/download-url/", **outsider_headers),
        api.delete(f"/api/v1/files/{own.id}/", **outsider_headers),
    ):
        assert response.status_code == 404
    removed = api.delete(f"/api/v1/files/{own.id}/", **accountant_headers)
    assert removed.status_code == 200, removed.json()
    assert removed.json()["data"]["retention_status"] == "DELETED"

    pending = api.post(
        "/api/v1/files/upload-intents/",
        _receipt_body(str(gym.id), image),
        format="json",
        **reception_headers,
    )
    assert pending.status_code == 201, pending.json()
    shared = _asset(gym, pending.json()["data"]["file_id"])
    _stage(store, shared, image)
    hidden_complete = _complete(api, other_headers, shared, image)
    assert hidden_complete.status_code == 404
    finished = _complete(api, accountant_headers, shared, image)
    assert finished.status_code == 200, finished.json()
    assert api.get(f"/api/v1/files/{shared.id}/download-url/", **other_headers).status_code == 404
    own_download = api.get(f"/api/v1/files/{shared.id}/download-url/", **reception_headers)
    assert own_download.status_code == 200, own_download.json()
    finance_download = api.get(f"/api/v1/files/{shared.id}/download-url/", **accountant_headers)
    assert finance_download.status_code == 200, finance_download.json()

    drafted = api.post(
        "/api/v1/expenses/",
        {
            "category_id": _category_id(gym),
            "business_date": gym_today(gym).isoformat(),
            "description": "Paper",
            "amount": "12.00",
            "currency": gym.currency,
            "payment_method": "BANK_TRANSFER",
            "receipt_file_asset_id": str(shared.id),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"receipt-draft-{uuid4().hex}",
        **reception_headers,
    )
    assert drafted.status_code == 201, drafted.json()
    assert api.get(f"/api/v1/files/{shared.id}/download-url/", **other_headers).status_code == 404
    attached_download = api.get(f"/api/v1/files/{shared.id}/download-url/", **reception_headers)
    assert attached_download.status_code == 200, attached_download.json()
    assert api.delete(f"/api/v1/files/{shared.id}/", **other_headers).status_code == 404
    attached_delete = api.delete(f"/api/v1/files/{shared.id}/", **reception_headers)
    assert attached_delete.status_code == 409
    assert attached_delete.json()["error"]["code"] == "FILE_STATE_CONFLICT"
    accountant_attached = api.delete(f"/api/v1/files/{shared.id}/", **accountant_headers)
    assert accountant_attached.status_code == 409
    assert accountant_attached.json()["error"]["code"] == "FILE_STATE_CONFLICT"

    loose = api.post(
        "/api/v1/files/upload-intents/",
        _receipt_body(str(gym.id), image),
        format="json",
        **accountant_headers,
    )
    assert loose.status_code == 201, loose.json()
    unattached = _asset(gym, loose.json()["data"]["file_id"])
    _stage(store, unattached, image)
    assert _complete(api, accountant_headers, unattached, image).status_code == 200
    hidden = api.get(f"/api/v1/files/{unattached.id}/download-url/", **reception_headers)
    assert hidden.status_code == 404
    assert api.delete(f"/api/v1/files/{unattached.id}/", **reception_headers).status_code == 404
    deleted = api.delete(f"/api/v1/files/{unattached.id}/", **accountant_headers)
    assert deleted.status_code == 200, deleted.json()


@_FAKES
def test_profile_photo_authorization_is_unchanged() -> None:
    store = MemoryStore()
    use_fakes(store=store)
    api, gym, owner_headers = _gym(f"PHR{uuid4().hex[:8].upper()}")
    image = _png()
    member = api.post("/api/v1/members/", _member_body(), format="json", **owner_headers)
    assert member.status_code == 201, member.json()
    member_id = member.json()["data"]["id"]
    accountant = add_staff(
        gym, email=f"photo-acct-{uuid4().hex[:8]}@example.test", role_key="ACCOUNTANT"
    )
    trainer = add_staff(
        gym, email=f"photo-trainer-{uuid4().hex[:8]}@example.test", role_key="TRAINER"
    )
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))

    forbidden = api.post(
        "/api/v1/files/upload-intents/",
        _profile_intent(member_id, image),
        format="json",
        **accountant_headers,
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
    created = api.post(
        "/api/v1/files/upload-intents/",
        _profile_intent(member_id, image),
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    asset = _asset(gym, created.json()["data"]["file_id"])
    _stage(store, asset, image)
    accountant_complete = _complete(api, accountant_headers, asset, image)
    accountant_delete = api.delete(f"/api/v1/files/{asset.id}/", **accountant_headers)
    assert accountant_complete.status_code == 403
    assert accountant_delete.status_code == 403
    assert accountant_complete.json()["error"]["code"] == "PERMISSION_DENIED"
    assert accountant_delete.json()["error"]["code"] == "PERMISSION_DENIED"
    trainer_complete = _complete(api, trainer_headers, asset, image)
    trainer_download = api.get(f"/api/v1/files/{asset.id}/download-url/", **trainer_headers)
    trainer_delete = api.delete(f"/api/v1/files/{asset.id}/", **trainer_headers)
    assert trainer_complete.status_code == 404
    assert trainer_download.status_code == 404
    assert trainer_delete.status_code == 404

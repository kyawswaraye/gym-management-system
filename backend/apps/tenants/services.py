from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    GymSettings,
    Role,
    RolePermission,
    SystemRoleTemplate,
)


@dataclass(frozen=True)
class ProvisionedGym:
    gym: Gym
    owner_membership: GymMembership


@transaction.atomic
def increment_membership_authz_version(*, gym_id: uuid.UUID, membership_id: uuid.UUID) -> int:
    """Atomically invalidate access tokens issued for one gym membership."""

    membership = GymMembership.objects.select_for_update().get(
        gym_id=gym_id,
        id=membership_id,
    )
    membership.authz_version += 1
    membership.save(update_fields=["authz_version", "updated_at"])
    return membership.authz_version


@transaction.atomic
def provision_gym(*, code: str, name: str, owner_email: str) -> ProvisionedGym:
    """Idempotently create a complete gym and its initial owner."""

    user_model = get_user_model()
    gym, _ = Gym.objects.get_or_create(
        code=code.strip().upper(),
        defaults={"name": name.strip(), "status": Gym.Status.ACTIVE},
    )
    if gym.name != name.strip():
        raise ValueError("The gym code is already assigned to another gym.")

    normalized_email = owner_email.strip().lower()
    owner, owner_created = user_model.objects.get_or_create(
        normalized_email=normalized_email,
        defaults={
            "email": normalized_email,
            "is_active": True,
        },
    )
    if owner_created:
        owner.set_unusable_password()
        owner.save(update_fields=["password", "updated_at"])
    set_gym_context(gym_id=gym.id)
    membership, _ = GymMembership.objects.get_or_create(
        gym=gym,
        user=owner,
        defaults={
            "status": GymMembership.Status.ACTIVE,
            "is_owner": True,
            "joined_date": date.today(),
        },
    )
    if not membership.is_owner or membership.status != GymMembership.Status.ACTIVE:
        membership.is_owner = True
        membership.status = GymMembership.Status.ACTIVE
        membership.save(update_fields=["is_owner", "status", "updated_at"])

    GymSettings.objects.get_or_create(gym=gym, defaults={"created_by_membership_id": membership.id})
    for template in SystemRoleTemplate.objects.filter(is_active=True).prefetch_related(
        "permissions"
    ):
        role, _ = Role.objects.get_or_create(
            gym=gym,
            key=template.key,
            defaults={
                "name": template.name,
                "description": template.description,
                "source_template": template,
                "source_template_version": template.version,
                "created_by_membership_id": membership.id,
            },
        )
        permission_ids = template.permissions.values_list("id", flat=True)
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    gym=gym,
                    role=role,
                    permission_id=permission_id,
                    created_by_membership_id=membership.id,
                )
                for permission_id in permission_ids
            ],
            ignore_conflicts=True,
        )
        if template.key == "OWNER":
            GymMembershipRole.objects.get_or_create(
                gym=gym,
                gym_membership=membership,
                role=role,
                defaults={"created_by_membership_id": membership.id},
            )

    for code_value, label in (
        ("members", "Members"),
        ("memberships", "Memberships"),
        ("finance", "Finance"),
        ("reports", "Reports"),
    ):
        feature, _ = Feature.objects.get_or_create(
            code=code_value,
            defaults={"name": label, "description": f"{label} module"},
        )
        GymFeatureEntitlement.objects.get_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "created_by_membership_id": membership.id,
            },
        )
    return ProvisionedGym(gym=gym, owner_membership=membership)

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers

from apps.audit.models import Outcome
from apps.audit.services import redact_audit_values, write_platform_audit, write_tenant_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.codes import GYM_CODE, resolve_code
from apps.common.db.tenant_context import set_gym_context
from apps.common.mail.invitations import render_invitation_email
from apps.tenants.exceptions import (
    IdempotencyKeyReused,
    InvalidGymTransition,
    InvalidInvitation,
    OwnerCredentialRequired,
    OwnerWorkflowConflict,
    ProvisioningConflict,
    ProvisioningValidationFailed,
    SettingsManagementForbidden,
    SubscriptionPlanInUse,
)
from apps.tenants.models import (
    RECEIPT_NUMBER_PREFIX,
    ExpenseCategory,
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    GymProvisioningRequest,
    GymSettings,
    Role,
    RolePermission,
    StaffInvitation,
    StaffInvitationRole,
    SubscriptionPlan,
    SubscriptionPlanFeature,
    SystemRoleTemplate,
    TenantSequence,
)
from apps.tenants.sequence_services import (
    EXPENSE_CATEGORY_CATALOG as EXPENSE_CATEGORY_CATALOG,
)
from apps.tenants.sequence_services import (
    ensure_protected_expense_categories,
    ensure_receipt_sequence,
)
from apps.tenants.validation import (
    GYM_PROFILE_FIELDS,
    prepare_profile_changes,
    prepare_settings_changes,
)
from apps.users.identity import (
    InvitedIdentityError,
    get_or_create_invited_identity,
    set_invitation_password,
)
from apps.users.models import RefreshSession


@dataclass(frozen=True)
class ProvisionedGym:
    gym: Gym
    owner_membership: GymMembership


@dataclass(frozen=True)
class AdminProvisioningResult:
    data: dict[str, object]
    replayed: bool


PROVISIONING_ROUTE = "/api/v1/admin/gyms/"


def _hash_invitation_token(token: str) -> str:
    if not settings.INVITATION_TOKEN_PEPPER:
        raise RuntimeError("INVITATION_TOKEN_PEPPER is not configured")
    return hmac.new(
        settings.INVITATION_TOKEN_PEPPER.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def _create_pending_owner_invitation(
    *,
    gym: Gym,
    membership: GymMembership,
    email: str,
    normalized_email: str,
    inviter_membership_id: uuid.UUID | None,
) -> tuple[StaffInvitation, str]:
    """Create one hashed owner invitation and return its plaintext token once."""

    plaintext = secrets.token_urlsafe(48)
    owner_role = Role.objects.get(gym=gym, key="OWNER", is_active=True)
    invitation = StaffInvitation.objects.create(
        gym=gym,
        gym_membership=membership,
        email=email,
        normalized_email=normalized_email,
        token_hash=_hash_invitation_token(plaintext),
        inviter_membership_id=inviter_membership_id,
        expires_at=timezone.now() + timedelta(seconds=settings.INVITATION_TTL_SECONDS),
    )
    StaffInvitationRole.objects.create(
        gym=gym,
        staff_invitation=invitation,
        role=owner_role,
    )
    return invitation, plaintext


def _deliver_owner_invitation(
    *,
    gym: Gym,
    membership: GymMembership,
    actor: Any,
    actor_membership_id: uuid.UUID | None,
    normalized_email: str,
    recipient: str,
    request_id: str,
    platform_assisted: bool,
    credential_recovery: bool,
    ip_address: str | None,
    user_agent: str,
) -> StaffInvitation:
    """Persist one hashed invitation, audit it, and email its acceptance link."""

    invitation, plaintext = _create_pending_owner_invitation(
        gym=gym,
        membership=membership,
        email=recipient,
        normalized_email=normalized_email,
        inviter_membership_id=actor_membership_id,
    )
    audit_values: dict[str, Any] = {
        "email": normalized_email,
        "status": invitation.status,
    }
    platform_details: dict[str, Any] = {"email": normalized_email}
    if credential_recovery:
        audit_values["invitation_reissue"] = True
        platform_details["invitation_reissue"] = True
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=actor_membership_id,
        action="OWNER_INVITED",
        target_type="StaffInvitation",
        target_id=invitation.id,
        request_id=request_id,
        new_values=audit_values,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if platform_assisted:
        write_platform_audit(
            actor=actor,
            action="OWNER_INVITED",
            module="owners",
            target_gym=gym,
            target_type="StaffInvitation",
            target_id=invitation.id,
            request_id=request_id,
            outcome=Outcome.SUCCESS,
            details=platform_details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    _send_owner_acceptance_email(
        gym_name=gym.name,
        recipient=recipient,
        plaintext_token=plaintext,
    )
    return invitation


def _send_owner_acceptance_email(*, gym_name: str, recipient: str, plaintext_token: str) -> None:
    acceptance_url = (
        f"{settings.APP_PUBLIC_URL.rstrip('/')}/accept-owner-invitation?token={plaintext_token}"
    )
    invitation_mail = render_invitation_email(
        gym_name=gym_name,
        acceptance_url=acceptance_url,
        role="owner",
        ttl_hours=max(1, settings.INVITATION_TTL_SECONDS // 3600),
    )
    send_mail(
        subject=invitation_mail.subject,
        message=invitation_mail.text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
        html_message=invitation_mail.html,
    )


OWNER_INVITATION_PENDING_MESSAGE = (
    "A pending owner invitation already exists. A new link can be issued after it expires."
)


def owner_invitation_data(invitation: StaffInvitation) -> dict[str, object]:
    return {
        "id": invitation.id,
        "gym_membership_id": invitation.gym_membership_id,
        "email": invitation.email,
        "status": invitation.status,
        "expires_at": invitation.expires_at,
    }


def _pending_invitation_membership_ids(gym: Gym):
    return StaffInvitation.objects.filter(
        gym=gym,
        status=StaffInvitation.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).values("gym_membership_id")


def visible_owner_memberships(gym: Gym) -> list[GymMembership]:
    """List owners, keeping an invite only while its link can still be accepted."""

    pending_membership_ids = _pending_invitation_membership_ids(gym)
    return list(
        GymMembership.objects.filter(gym=gym, is_owner=True)
        .select_related("user")
        .filter(~Q(status=GymMembership.Status.INVITED) | Q(id__in=pending_membership_ids))
        .order_by("user__normalized_email", "id")
    )


def visible_login_memberships(gym: Gym) -> list[GymMembership]:
    """List Owner and staff login accounts for Super Admin assistance.

    Shows login emails only. Member customer records stay outside this read.
    Expired invites leave the list; live pending invites remain.
    """

    pending_membership_ids = _pending_invitation_membership_ids(gym)
    return list(
        GymMembership.objects.filter(gym=gym)
        .exclude(status=GymMembership.Status.REVOKED)
        .select_related("user")
        .prefetch_related("gymmembershiprole_set__role")
        .filter(~Q(status=GymMembership.Status.INVITED) | Q(id__in=pending_membership_ids))
        .order_by("-is_owner", "user__normalized_email", "id")
    )


def owner_membership_data(membership: GymMembership) -> dict[str, object]:
    return {
        "gym_membership_id": membership.id,
        "user_id": membership.user_id,
        "email": membership.user.email,
        "first_name": membership.user.first_name,
        "last_name": membership.user.last_name,
        "phone": membership.user.phone,
        "status": membership.status,
        "is_owner": membership.is_owner,
        "authz_version": membership.authz_version,
        "invited_at": membership.invited_at,
        "activated_at": membership.activated_at,
    }


def login_account_data(membership: GymMembership) -> dict[str, object]:
    role_keys = sorted(
        {
            assignment.role.key
            for assignment in membership.gymmembershiprole_set.all()
            if assignment.role.is_active
        }
    )
    return {
        **owner_membership_data(membership),
        "role_keys": role_keys,
    }


@transaction.atomic
def invite_owner(
    *,
    gym_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID | None,
    owner_data: dict[str, Any],
    request_id: str,
    platform_assisted: bool = False,
    ip_address: str | None = None,
    user_agent: str = "",
) -> tuple[StaffInvitation, bool]:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    if gym.status == Gym.Status.CLOSED:
        raise OwnerWorkflowConflict("Owners cannot be invited to a closed gym.")
    set_gym_context(gym_id=gym.id)
    normalized_email = str(owner_data["email"]).strip().lower()
    StaffInvitation.objects.filter(
        gym=gym,
        normalized_email=normalized_email,
        status=StaffInvitation.Status.PENDING,
        expires_at__lte=timezone.now(),
    ).update(status=StaffInvitation.Status.EXPIRED)
    existing_invitation = (
        StaffInvitation.objects.select_related("gym_membership")
        .filter(
            gym=gym,
            normalized_email=normalized_email,
            status=StaffInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if existing_invitation:
        return existing_invitation, False

    user = get_or_create_invited_identity(
        email=normalized_email,
        first_name=str(owner_data["first_name"]).strip(),
        last_name=str(owner_data["last_name"]).strip(),
        phone=str(owner_data.get("phone", "")).strip(),
    )
    if not user.is_active or user.is_platform_admin:
        raise OwnerWorkflowConflict("This identity cannot be linked as an Owner.")

    now = timezone.now()
    membership = GymMembership.objects.select_for_update().filter(gym=gym, user=user).first()
    if (
        membership is not None
        and membership.is_owner
        and membership.status == GymMembership.Status.ACTIVE
    ):
        if not platform_assisted or user.has_usable_password():
            raise OwnerWorkflowConflict("This identity is already an active Owner.")
        try:
            with transaction.atomic():
                invitation = _deliver_owner_invitation(
                    gym=gym,
                    membership=membership,
                    actor=actor,
                    actor_membership_id=actor_membership_id,
                    normalized_email=normalized_email,
                    recipient=str(user.email),
                    request_id=request_id,
                    platform_assisted=True,
                    credential_recovery=True,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        except IntegrityError:
            pending = (
                StaffInvitation.objects.select_for_update()
                .filter(
                    gym=gym,
                    normalized_email=normalized_email,
                    status=StaffInvitation.Status.PENDING,
                    expires_at__gt=timezone.now(),
                )
                .first()
            )
            if pending is None:
                raise
            return pending, False
        return invitation, True

    if membership is None:
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.INVITED,
            is_owner=True,
            joined_date=datetime.now(ZoneInfo(gym.timezone)).date(),
            invited_at=now,
            created_by_membership_id=actor_membership_id,
        )
    elif not membership.is_owner:
        membership.invited_at = now
        membership.save(update_fields=["invited_at", "updated_at"])

    invitation = _deliver_owner_invitation(
        gym=gym,
        membership=membership,
        actor=actor,
        actor_membership_id=actor_membership_id,
        normalized_email=normalized_email,
        recipient=str(user.email),
        request_id=request_id,
        platform_assisted=platform_assisted,
        credential_recovery=False,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return invitation, True


def _invitation_bootstrap(token_hash: str) -> tuple[uuid.UUID, uuid.UUID]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT invitation_id, gym_id FROM app_owner_invitation(%s)", [token_hash])
        row = cursor.fetchone()
    if row is None:
        raise InvalidInvitation()
    return uuid.UUID(str(row[0])), uuid.UUID(str(row[1]))


@transaction.atomic
def accept_owner_invitation(
    *,
    plaintext_token: str,
    password: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> GymMembership:
    token_hash = _hash_invitation_token(plaintext_token)
    invitation_id, gym_id = _invitation_bootstrap(token_hash)
    set_gym_context(gym_id=gym_id)
    try:
        invitation = (
            StaffInvitation.objects.select_for_update()
            .select_related("gym_membership__user", "gym")
            .get(id=invitation_id, gym_id=gym_id)
        )
    except StaffInvitation.DoesNotExist as exc:
        raise InvalidInvitation() from exc
    now = timezone.now()
    if invitation.status != StaffInvitation.Status.PENDING or invitation.expires_at <= now:
        raise InvalidInvitation()
    membership = GymMembership.objects.select_for_update().get(
        id=invitation.gym_membership_id,
        gym_id=gym_id,
    )
    if not membership.is_owner:
        raise InvalidInvitation()
    try:
        owner_role = (
            StaffInvitationRole.objects.select_related("role")
            .get(
                gym_id=gym_id,
                staff_invitation=invitation,
                role__key="OWNER",
            )
            .role
        )
    except StaffInvitationRole.DoesNotExist as exc:
        raise InvalidInvitation() from exc
    gym = Gym.objects.select_for_update().get(id=gym_id)
    if gym.status != Gym.Status.ACTIVE:
        raise OwnerWorkflowConflict("The gym must be active before invitation acceptance.")
    user = membership.user
    if user.has_usable_password():
        if not user.check_password(password):
            raise InvalidInvitation()
    else:
        try:
            set_invitation_password(
                token_hash=token_hash,
                user=user,
                password=password,
                changed_at=now,
            )
        except InvitedIdentityError as exc:
            raise InvalidInvitation() from exc
    membership.status = GymMembership.Status.ACTIVE
    membership.is_owner = True
    membership.activated_at = now
    membership.revoked_at = None
    membership.authz_version += 1
    membership.save(
        update_fields=[
            "status",
            "is_owner",
            "activated_at",
            "revoked_at",
            "authz_version",
            "updated_at",
        ]
    )
    GymMembershipRole.objects.get_or_create(
        gym_id=gym_id,
        gym_membership=membership,
        role=owner_role,
        defaults={"created_by_membership_id": invitation.inviter_membership_id},
    )
    invitation.status = StaffInvitation.Status.ACCEPTED
    invitation.accepted_at = now
    invitation.accepted_by_user = user
    invitation.save(update_fields=["status", "accepted_at", "accepted_by_user", "updated_at"])
    write_tenant_audit(
        gym=gym,
        actor_user=user,
        actor_membership_id=membership.id,
        action="OWNER_INVITATION_ACCEPTED",
        target_type="GymMembership",
        target_id=membership.id,
        request_id=request_id,
        new_values={"status": membership.status, "is_owner": True},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return membership


@transaction.atomic
def revoke_owner(
    *,
    gym_id: uuid.UUID,
    target_membership_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    reason: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> GymMembership:
    Gym.objects.select_for_update().get(id=gym_id)
    set_gym_context(gym_id=gym_id)
    GymMembership.objects.select_for_update().get(
        id=actor_membership_id,
        gym_id=gym_id,
        user=actor,
        is_owner=True,
        status=GymMembership.Status.ACTIVE,
    )
    if target_membership_id == actor_membership_id:
        raise OwnerWorkflowConflict("Owners cannot revoke their own access.")
    target = (
        GymMembership.objects.select_for_update()
        .select_related("user", "gym")
        .get(
            gym_id=gym_id,
            id=target_membership_id,
            is_owner=True,
            status=GymMembership.Status.ACTIVE,
        )
    )
    remaining = GymMembership.objects.filter(
        gym_id=gym_id,
        is_owner=True,
        status=GymMembership.Status.ACTIVE,
    ).exclude(id=target.id)
    if not remaining.exists():
        raise OwnerWorkflowConflict("At least one active Owner must remain.")
    now = timezone.now()
    target.status = GymMembership.Status.REVOKED
    target.revoked_at = now
    target.authz_version += 1
    target.save(update_fields=["status", "revoked_at", "authz_version", "updated_at"])
    RefreshSession.objects.filter(
        gym_membership_id=target.id,
        revoked_at__isnull=True,
    ).update(revoked_at=now, revocation_reason="OWNER_REVOKED")
    write_tenant_audit(
        gym=target.gym,
        actor_user=actor,
        actor_membership_id=actor_membership_id,
        action="OWNER_REVOKED",
        target_type="GymMembership",
        target_id=target.id,
        request_id=request_id,
        old_values={"status": GymMembership.Status.ACTIVE},
        new_values={"status": target.status, "reason": reason},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return target


CANCELLED_INVITATION_REASON = "Cancelled before acceptance."


@transaction.atomic
def cancel_owner_invitation(
    *,
    gym_id: uuid.UUID,
    target_membership_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> GymMembership:
    """Stop a pending owner invitation and drop that person from the owner list."""

    Gym.objects.select_for_update().get(id=gym_id)
    set_gym_context(gym_id=gym_id)
    GymMembership.objects.select_for_update().get(
        id=actor_membership_id,
        gym_id=gym_id,
        user=actor,
        is_owner=True,
        status=GymMembership.Status.ACTIVE,
    )
    target = (
        GymMembership.objects.select_for_update()
        .select_related("user", "gym")
        .get(
            gym_id=gym_id,
            id=target_membership_id,
            is_owner=True,
            status=GymMembership.Status.INVITED,
        )
    )
    now = timezone.now()
    pending = list(
        StaffInvitation.objects.select_for_update().filter(
            gym_id=gym_id,
            gym_membership=target,
            status=StaffInvitation.Status.PENDING,
        )
    )
    for invitation in pending:
        invitation.status = StaffInvitation.Status.REVOKED
        invitation.revoked_at = now
        invitation.revoked_by_membership_id = actor_membership_id
        invitation.revocation_reason = CANCELLED_INVITATION_REASON
        invitation.save(
            update_fields=[
                "status",
                "revoked_at",
                "revoked_by_membership_id",
                "revocation_reason",
                "updated_at",
            ]
        )
    if pending:
        write_tenant_audit(
            gym=target.gym,
            actor_user=actor,
            actor_membership_id=actor_membership_id,
            action="OWNER_INVITATION_CANCELLED",
            target_type="GymMembership",
            target_id=target.id,
            request_id=request_id,
            new_values={
                "email": target.user.email,
                "status": StaffInvitation.Status.REVOKED,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return target


def canonical_provisioning_hash(data: dict[str, object]) -> str:
    canonical = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _copy_roles(*, gym: Gym, owner_membership: GymMembership) -> list[Role]:
    roles: list[Role] = []
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
                "created_by_membership_id": owner_membership.id,
            },
        )
        roles.append(role)
        permission_ids = template.permissions.values_list("id", flat=True)
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    gym=gym,
                    role=role,
                    permission_id=permission_id,
                    created_by_membership_id=owner_membership.id,
                )
                for permission_id in permission_ids
            ],
            ignore_conflicts=True,
        )
        if template.key == "OWNER":
            GymMembershipRole.objects.get_or_create(
                gym=gym,
                gym_membership=owner_membership,
                role=role,
                defaults={"created_by_membership_id": owner_membership.id},
            )
    return roles


def _seed_categories(*, gym: Gym, actor_membership: GymMembership) -> list[ExpenseCategory]:
    return ensure_protected_expense_categories(
        gym=gym,
        actor_membership_id=actor_membership.id,
    )


def _seed_receipt_sequence(
    *, gym: Gym, settings: GymSettings, actor_membership: GymMembership
) -> TenantSequence:
    return ensure_receipt_sequence(
        gym=gym,
        prefix=settings.receipt_number_prefix,
        actor_membership_id=actor_membership.id,
    )


def _claim_provisioning_request(
    *,
    actor: Any,
    idempotency_key: str,
    request_hash: str,
) -> tuple[GymProvisioningRequest, bool]:
    try:
        with transaction.atomic():
            record = GymProvisioningRequest.objects.create(
                actor_user=actor,
                method="POST",
                route=PROVISIONING_ROUTE,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        return record, False
    except IntegrityError:
        record = GymProvisioningRequest.objects.select_for_update().get(
            actor_user=actor,
            method="POST",
            route=PROVISIONING_ROUTE,
            idempotency_key=idempotency_key,
        )
        if record.request_hash != request_hash:
            raise IdempotencyKeyReused() from None
        return record, record.state == GymProvisioningRequest.State.COMPLETED


def _provisioning_response(
    *,
    gym: Gym,
    owner: Any,
    membership: GymMembership,
    roles: list[Role],
    entitlements: list[GymFeatureEntitlement],
    categories: list[ExpenseCategory],
) -> dict[str, object]:
    return {
        "gym": {
            "id": str(gym.id),
            "code": gym.code,
            "name": gym.name,
            "legal_name": gym.legal_name,
            "status": gym.status,
            "email": gym.email,
            "phone": gym.phone,
            "address_line1": gym.address_line1,
            "address_line2": gym.address_line2,
            "city": gym.city,
            "region": gym.region,
            "postal_code": gym.postal_code,
            "country_code": gym.country_code,
            "timezone": gym.timezone,
            "currency": gym.currency,
            "subscription_plan_id": str(gym.subscription_plan_id),
            "subscription_status": gym.subscription_status,
            "subscription_starts_at": (
                gym.subscription_starts_at.isoformat() if gym.subscription_starts_at else None
            ),
            "subscription_ends_at": (
                gym.subscription_ends_at.isoformat() if gym.subscription_ends_at else None
            ),
        },
        "owner": {
            "user": {
                "id": str(owner.id),
                "email": owner.email,
                "first_name": owner.first_name,
                "last_name": owner.last_name,
                "phone": owner.phone,
            },
            "gym_membership": {
                "id": str(membership.id),
                "status": membership.status,
                "is_owner": membership.is_owner,
            },
        },
        "roles": [
            {"id": str(role.id), "key": role.key, "name": role.name}
            for role in sorted(roles, key=lambda item: item.key)
        ],
        "feature_entitlements": [
            {
                "feature": entitlement.feature.code,
                "enabled": entitlement.enabled,
                "limit_value": entitlement.limit_value,
                "source": entitlement.source,
            }
            for entitlement in sorted(entitlements, key=lambda item: item.feature.code)
        ],
        "expense_categories": [
            {
                "id": str(category.id),
                "code": category.code,
                "system_key": category.system_key,
                "name": category.name,
                "is_active": category.is_active,
            }
            for category in sorted(categories, key=lambda item: item.display_order)
        ],
    }


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
def provision_gym_from_admin(
    *,
    actor: Any,
    idempotency_key: str,
    request_hash: str,
    gym_data: dict[str, Any],
    owner_data: dict[str, Any],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> AdminProvisioningResult:
    record, replayed = _claim_provisioning_request(
        actor=actor,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replayed:
        return AdminProvisioningResult(data=dict(record.response_body), replayed=True)

    code = str(gym_data.get("code") or "").strip().upper()
    if not code:
        code = resolve_code(
            "",
            Gym.objects.values_list("code", flat=True),
            str(gym_data["name"]),
            prefix="GYM",
            separator="-",
            pattern=GYM_CODE,
        )
    timezone_name = str(gym_data["timezone"])
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ProvisioningValidationFailed("Unsupported timezone.") from exc

    currency = str(gym_data["currency"]).strip().upper()
    plan = SubscriptionPlan.objects.filter(
        id=gym_data["subscription_plan_id"],
        status=SubscriptionPlan.Status.ACTIVE,
    ).first()
    if plan is None:
        raise ProvisioningValidationFailed("The subscription plan is not active.")
    if plan.currency != currency:
        raise ProvisioningValidationFailed(
            "Gym currency must match the subscription plan currency."
        )

    starts_at = gym_data.get("subscription_starts_at")
    ends_at = gym_data.get("subscription_ends_at")
    if starts_at and ends_at and ends_at <= starts_at:
        raise ProvisioningValidationFailed("Subscription end must be after its start.")
    if Gym.objects.filter(code=code).exists():
        raise ProvisioningConflict("The gym code is already in use.")
    try:
        with transaction.atomic():
            gym = Gym.objects.create(
                code=code,
                name=str(gym_data["name"]).strip(),
                legal_name=str(gym_data.get("legal_name", "")).strip(),
                status=Gym.Status.ACTIVE,
                email=str(gym_data.get("email", "")).strip().lower(),
                phone=str(gym_data.get("phone", "")).strip(),
                address_line1=str(gym_data.get("address_line1", "")).strip(),
                address_line2=str(gym_data.get("address_line2", "")).strip(),
                city=str(gym_data.get("city", "")).strip(),
                region=str(gym_data.get("region", "")).strip(),
                postal_code=str(gym_data.get("postal_code", "")).strip(),
                country_code=str(gym_data.get("country_code", "")).strip().upper(),
                timezone=timezone_name,
                currency=currency,
                subscription_plan=plan,
                subscription_status=gym_data.get(
                    "subscription_status", Gym.SubscriptionStatus.TRIAL
                ),
                subscription_starts_at=starts_at,
                subscription_ends_at=ends_at,
            )
    except IntegrityError as exc:
        raise ProvisioningConflict("The gym code is already in use.") from exc

    send_invitation = bool(owner_data.get("send_invitation", True))
    normalized_email = str(owner_data["email"]).strip().lower()
    owner_existed = get_user_model().objects.filter(normalized_email=normalized_email).exists()
    if not owner_existed and not send_invitation:
        raise OwnerCredentialRequired()
    owner = get_or_create_invited_identity(
        email=normalized_email,
        first_name=str(owner_data["first_name"]).strip(),
        last_name=str(owner_data["last_name"]).strip(),
        phone=str(owner_data.get("phone", "")).strip(),
    )
    if not owner.is_active:
        raise ProvisioningConflict("The owner account is inactive.")
    if owner.is_platform_admin:
        raise ProvisioningValidationFailed(
            "A platform administrator identity cannot be linked as a tenant Owner."
        )
    if not send_invitation and not owner.has_usable_password():
        raise OwnerCredentialRequired()

    set_gym_context(gym_id=gym.id)
    activated_at = datetime.now(UTC)
    membership = GymMembership.objects.create(
        gym=gym,
        user=owner,
        status=GymMembership.Status.ACTIVE,
        is_owner=True,
        joined_date=datetime.now(ZoneInfo(gym.timezone)).date(),
        invited_at=activated_at if send_invitation else None,
        activated_at=activated_at,
    )
    settings = GymSettings.objects.create(
        gym=gym,
        receipt_number_prefix=RECEIPT_NUMBER_PREFIX,
        created_by_membership_id=membership.id,
    )
    roles = _copy_roles(gym=gym, owner_membership=membership)

    entitlements = []
    for plan_feature in SubscriptionPlanFeature.objects.filter(
        subscription_plan=plan
    ).select_related("feature"):
        entitlement = GymFeatureEntitlement.objects.create(
            gym=gym,
            feature=plan_feature.feature,
            enabled=plan_feature.enabled,
            limit_value=plan_feature.limit_value,
            source=GymFeatureEntitlement.Source.PLAN,
            effective_from=datetime.now(UTC),
            created_by_membership_id=membership.id,
        )
        entitlements.append(entitlement)
    categories = _seed_categories(gym=gym, actor_membership=membership)
    _seed_receipt_sequence(gym=gym, settings=settings, actor_membership=membership)

    invitation: StaffInvitation | None = None
    plaintext_token = ""
    if send_invitation:
        invitation, plaintext_token = _create_pending_owner_invitation(
            gym=gym,
            membership=membership,
            email=str(owner.email),
            normalized_email=normalized_email,
            inviter_membership_id=None,
        )
    response_data = _provisioning_response(
        gym=gym,
        owner=owner,
        membership=membership,
        roles=roles,
        entitlements=entitlements,
        categories=categories,
    )
    audit_details: dict[str, Any] = {
        "gym_code": gym.code,
        "subscription_plan_id": str(plan.id),
        "owner_user_id": str(owner.id),
        "send_invitation": send_invitation,
    }
    if invitation is not None:
        audit_details["owner_invitation_id"] = str(invitation.id)
        write_tenant_audit(
            gym=gym,
            actor_user=actor,
            actor_membership_id=None,
            action="OWNER_INVITED",
            target_type="StaffInvitation",
            target_id=invitation.id,
            request_id=request_id,
            new_values={
                "status": invitation.status,
                "gym_membership_id": str(membership.id),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
    write_platform_audit(
        actor=actor,
        action="GYM_PROVISIONED",
        module="platform",
        target_gym=gym,
        target_type="Gym",
        target_id=gym.id,
        request_id=request_id,
        outcome=Outcome.SUCCESS,
        details=audit_details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    record.gym = gym
    record.state = GymProvisioningRequest.State.COMPLETED
    record.response_status = 201
    record.response_body = response_data
    record.save(
        update_fields=[
            "gym",
            "state",
            "response_status",
            "response_body",
            "updated_at",
        ]
    )
    if invitation is not None:
        # Delivery stays inside the transaction so a send failure rolls back
        # the gym, membership, invitation, audits, and idempotency record.
        _send_owner_acceptance_email(
            gym_name=gym.name,
            recipient=str(owner.email),
            plaintext_token=plaintext_token,
        )
    return AdminProvisioningResult(data=response_data, replayed=False)


def admin_gym_detail(gym: Gym) -> dict[str, object]:
    return {
        "id": str(gym.id),
        "code": gym.code,
        "name": gym.name,
        "legal_name": gym.legal_name,
        "status": gym.status,
        "email": gym.email,
        "phone": gym.phone,
        "address_line1": gym.address_line1,
        "address_line2": gym.address_line2,
        "city": gym.city,
        "region": gym.region,
        "postal_code": gym.postal_code,
        "country_code": gym.country_code,
        "timezone": gym.timezone,
        "currency": gym.currency,
        "subscription_plan_id": (
            str(gym.subscription_plan_id) if gym.subscription_plan_id else None
        ),
        "subscription_plan_name": (gym.subscription_plan.name if gym.subscription_plan else None),
        "subscription_status": gym.subscription_status,
        "subscription_starts_at": (
            gym.subscription_starts_at.isoformat() if gym.subscription_starts_at else None
        ),
        "subscription_ends_at": (
            gym.subscription_ends_at.isoformat() if gym.subscription_ends_at else None
        ),
        "created_at": gym.created_at.isoformat(),
        "updated_at": gym.updated_at.isoformat(),
    }


def _revoke_gym_sessions(*, gym: Gym, reason: str) -> int:
    return RefreshSession.objects.filter(
        selected_gym=gym,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=reason[:64],
    )


def _reconcile_plan_entitlements(*, gym: Gym, plan: SubscriptionPlan) -> None:
    set_gym_context(gym_id=gym.id)
    plan_features = list(
        SubscriptionPlanFeature.objects.filter(subscription_plan=plan).select_related("feature")
    )
    feature_ids = {item.feature_id for item in plan_features}
    GymFeatureEntitlement.objects.filter(
        gym=gym,
        source=GymFeatureEntitlement.Source.PLAN,
    ).exclude(feature_id__in=feature_ids).update(enabled=False, limit_value=None)
    for plan_feature in plan_features:
        entitlement = GymFeatureEntitlement.objects.filter(
            gym=gym,
            feature=plan_feature.feature,
        ).first()
        if entitlement is not None and entitlement.source == GymFeatureEntitlement.Source.OVERRIDE:
            continue
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=plan_feature.feature,
            defaults={
                "enabled": plan_feature.enabled,
                "limit_value": plan_feature.limit_value,
                "source": GymFeatureEntitlement.Source.PLAN,
                "effective_from": timezone.now(),
                "effective_until": None,
            },
        )


@transaction.atomic
def update_gym_from_admin(
    *,
    gym_id: uuid.UUID,
    data: dict[str, Any],
    actor: Any,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> Gym:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    old_snapshot = admin_gym_detail(gym)
    plan = gym.subscription_plan
    plan_changed = False
    if "subscription_plan_id" in data:
        plan = SubscriptionPlan.objects.filter(
            id=data["subscription_plan_id"],
            status=SubscriptionPlan.Status.ACTIVE,
        ).first()
        if plan is None:
            raise ProvisioningValidationFailed("The subscription plan is not active.")
        plan_changed = plan.id != gym.subscription_plan_id

    currency = str(data.get("currency", gym.currency)).upper()
    if plan is not None and plan.currency != currency:
        raise ProvisioningValidationFailed(
            "Gym currency must match the subscription plan currency."
        )
    starts_at = data.get("subscription_starts_at", gym.subscription_starts_at)
    ends_at = data.get("subscription_ends_at", gym.subscription_ends_at)
    if starts_at and ends_at and ends_at <= starts_at:
        raise ProvisioningValidationFailed("Subscription end must be after its start.")

    mutable_fields = (
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
        "subscription_status",
        "subscription_starts_at",
        "subscription_ends_at",
    )
    changed_fields = []
    for field in mutable_fields:
        if field not in data:
            continue
        value = data[field]
        if field in {"country_code", "currency"}:
            value = str(value).upper()
        if getattr(gym, field) != value:
            setattr(gym, field, value)
            changed_fields.append(field)
    if plan_changed and plan is not None:
        gym.subscription_plan = plan
        changed_fields.append("subscription_plan")
    if changed_fields:
        gym.save(update_fields=[*changed_fields, "updated_at"])
        if plan_changed and plan is not None:
            _reconcile_plan_entitlements(gym=gym, plan=plan)
        if gym.subscription_status in {
            Gym.SubscriptionStatus.PAST_DUE,
            Gym.SubscriptionStatus.CANCELLED,
        }:
            _revoke_gym_sessions(gym=gym, reason="SUBSCRIPTION_INELIGIBLE")
        new_snapshot = admin_gym_detail(gym)
        audit_keys = [
            "subscription_plan_id" if field == "subscription_plan" else field
            for field in changed_fields
        ]
        write_platform_audit(
            actor=actor,
            action="GYM_UPDATED",
            module="platform",
            target_gym=gym,
            target_type="Gym",
            target_id=gym.id,
            request_id=request_id,
            outcome=Outcome.SUCCESS,
            old_values={key: old_snapshot[key] for key in audit_keys},
            details={key: new_snapshot[key] for key in audit_keys},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return gym


@transaction.atomic
def transition_gym_from_admin(
    *,
    gym_id: uuid.UUID,
    target_status: str,
    reason: str,
    actor: Any,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> tuple[Gym, bool]:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    if gym.status == target_status:
        return gym, False
    if target_status == Gym.Status.SUSPENDED:
        if gym.status != Gym.Status.ACTIVE:
            raise InvalidGymTransition()
        action = "GYM_SUSPENDED"
    elif target_status == Gym.Status.ACTIVE:
        if gym.status not in {Gym.Status.SUSPENDED, Gym.Status.PENDING}:
            raise InvalidGymTransition()
        if (
            gym.subscription_plan is None
            or gym.subscription_plan.status != SubscriptionPlan.Status.ACTIVE
            or gym.subscription_status
            not in {Gym.SubscriptionStatus.TRIAL, Gym.SubscriptionStatus.ACTIVE}
        ):
            raise InvalidGymTransition(
                "The gym requires an active eligible subscription before activation."
            )
        set_gym_context(gym_id=gym.id)
        has_owner = GymMembership.objects.filter(
            gym=gym,
            is_owner=True,
            status=GymMembership.Status.ACTIVE,
        ).exists()
        if not has_owner:
            raise InvalidGymTransition("The gym requires at least one active Owner.")
        action = "GYM_ACTIVATED"
    else:
        raise InvalidGymTransition()

    old_status = gym.status
    gym.status = target_status
    gym.save(update_fields=["status", "updated_at"])
    if target_status == Gym.Status.SUSPENDED:
        _revoke_gym_sessions(gym=gym, reason="GYM_SUSPENDED")
    write_platform_audit(
        actor=actor,
        action=action,
        module="platform",
        target_gym=gym,
        target_type="Gym",
        target_id=gym.id,
        request_id=request_id,
        outcome=Outcome.SUCCESS,
        old_values={"status": old_status},
        details={"status": target_status, "reason": reason},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return gym, True


GYM_STATUS_COUNT_FIELDS = ("total", "PENDING", "ACTIVE", "SUSPENDED", "CLOSED")
GYM_SUBSCRIPTION_COUNT_FIELDS = ("TRIAL", "ACTIVE", "PAST_DUE", "CANCELLED")
PLAN_STATUS_COUNT_FIELDS = ("ACTIVE", "RETIRED")
PLAN_METADATA_FIELDS = ("name", "status", "price", "billing_interval")
_ELIGIBLE_SUBSCRIPTION_STATUSES = (
    Gym.SubscriptionStatus.TRIAL,
    Gym.SubscriptionStatus.ACTIVE,
)
_PRICE_QUANTUM = Decimal("0.01")


def platform_dashboard_counts() -> dict[str, dict[str, int]]:
    """Return bounded platform counts from two aggregate queries.

    Gym status, gym subscription status, and plan status are counted in place.
    The queries do not join users, memberships, entitlements, or finance.
    """

    gym_counts = Gym.objects.aggregate(
        total=Count("id"),
        **{
            status: Count("id", filter=Q(status=status))
            for status in GYM_STATUS_COUNT_FIELDS
            if status != "total"
        },
        **{
            f"subscription_{status}": Count("id", filter=Q(subscription_status=status))
            for status in GYM_SUBSCRIPTION_COUNT_FIELDS
        },
    )
    plan_counts = SubscriptionPlan.objects.aggregate(
        **{status: Count("id", filter=Q(status=status)) for status in PLAN_STATUS_COUNT_FIELDS}
    )
    return {
        "gym_statuses": {
            "total": int(gym_counts["total"]),
            **{
                status: int(gym_counts[status])
                for status in GYM_STATUS_COUNT_FIELDS
                if status != "total"
            },
        },
        "gym_subscription_statuses": {
            status: int(gym_counts[f"subscription_{status}"])
            for status in GYM_SUBSCRIPTION_COUNT_FIELDS
        },
        "subscription_plan_statuses": {
            status: int(plan_counts[status]) for status in PLAN_STATUS_COUNT_FIELDS
        },
    }


def _plan_price(value: Any) -> Decimal:
    return Decimal(value).quantize(_PRICE_QUANTUM)


def _plan_audit_value(field: str, value: Any) -> Any:
    if field == "price":
        return format(_plan_price(value), "f")
    return value


@transaction.atomic
def update_subscription_plan_from_admin(
    *,
    plan_id: uuid.UUID,
    data: dict[str, Any],
    actor: Any,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> tuple[SubscriptionPlan, bool]:
    """Update safe plan metadata without touching tenant entitlements."""

    plan = SubscriptionPlan.objects.select_for_update().get(id=plan_id)
    changes: dict[str, Any] = {}
    for field in PLAN_METADATA_FIELDS:
        if field not in data:
            continue
        value = _plan_price(data[field]) if field == "price" else data[field]
        current = _plan_price(getattr(plan, field)) if field == "price" else getattr(plan, field)
        if current != value:
            changes[field] = value
    if not changes:
        return plan, False
    if changes.get("status") == SubscriptionPlan.Status.RETIRED:
        blocking_ids = list(
            Gym.objects.select_for_update()
            .filter(
                subscription_plan_id=plan.id,
                subscription_status__in=_ELIGIBLE_SUBSCRIPTION_STATUSES,
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        if blocking_ids:
            raise SubscriptionPlanInUse()

    old_payload: dict[str, Any] = {}
    new_payload: dict[str, Any] = {}
    for field, value in changes.items():
        old_payload[field] = _plan_audit_value(field, getattr(plan, field))
        setattr(plan, field, value)
        new_payload[field] = _plan_audit_value(field, value)
    plan.save(update_fields=[*changes.keys(), "updated_at"])
    write_platform_audit(
        actor=actor,
        action="SUBSCRIPTION_PLAN_UPDATED",
        module="platform",
        target_type="SubscriptionPlan",
        target_id=plan.id,
        request_id=request_id,
        outcome=Outcome.SUCCESS,
        old_values=cast(dict[str, Any], redact_audit_values(old_payload)),
        details=cast(dict[str, Any], redact_audit_values(new_payload)),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return plan, True


@transaction.atomic
def provision_gym(*, code: str, name: str, owner_email: str) -> ProvisionedGym:
    """Idempotently create a complete gym and its initial owner."""

    gym, _ = Gym.objects.get_or_create(
        code=code.strip().upper(),
        defaults={"name": name.strip(), "status": Gym.Status.ACTIVE},
    )
    if gym.name != name.strip():
        raise ValueError("The gym code is already assigned to another gym.")

    normalized_email = owner_email.strip().lower()
    owner = get_or_create_invited_identity(
        email=normalized_email,
        first_name="",
        last_name="",
        phone="",
    )
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

    settings, _ = GymSettings.objects.get_or_create(
        gym=gym,
        defaults={
            "receipt_number_prefix": RECEIPT_NUMBER_PREFIX,
            "created_by_membership_id": membership.id,
        },
    )
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
        ("pt", "Personal training"),
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
    _seed_categories(gym=gym, actor_membership=membership)
    _seed_receipt_sequence(gym=gym, settings=settings, actor_membership=membership)
    return ProvisionedGym(gym=gym, owner_membership=membership)


def _audit_copy(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    return value


def _require_active_gym(gym_id: uuid.UUID) -> Gym:
    gym = (
        Gym.objects.select_for_update(of=("self",))
        .select_related("subscription_plan")
        .get(id=gym_id)
    )
    if gym.status != Gym.Status.ACTIVE or gym.subscription_status not in {
        Gym.SubscriptionStatus.TRIAL,
        Gym.SubscriptionStatus.ACTIVE,
    }:
        raise AuthenticationFailed()
    return gym


def _require_active_membership(
    *, gym_id: uuid.UUID, actor: Any, actor_membership_id: uuid.UUID
) -> GymMembership:
    try:
        return GymMembership.objects.select_for_update().get(
            id=actor_membership_id,
            gym_id=gym_id,
            user_id=actor.id,
            status=GymMembership.Status.ACTIVE,
        )
    except GymMembership.DoesNotExist as exc:
        raise AuthenticationFailed() from exc


@transaction.atomic
def update_gym_profile(
    *,
    gym_id: uuid.UUID,
    data: dict[str, Any],
    actor: Any,
    actor_membership_id: uuid.UUID,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> Gym:
    """Update tenant-owned identity, contact, address, timezone, and currency."""

    changes = prepare_profile_changes(data)
    set_gym_context(gym_id=gym_id)
    try:
        gym = _require_active_gym(gym_id)
    except Gym.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    _require_active_membership(gym_id=gym_id, actor=actor, actor_membership_id=actor_membership_id)
    if "currency" in changes and gym.subscription_plan_id is not None:
        plan_currency = gym.subscription_plan.currency if gym.subscription_plan else None
        if plan_currency is not None and changes["currency"] != plan_currency:
            raise serializers.ValidationError(
                {"currency": ["Currency must match the subscription plan currency."]}
            )

    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    for field in GYM_PROFILE_FIELDS:
        if field not in changes or getattr(gym, field) == changes[field]:
            continue
        old_values[field] = _audit_copy(getattr(gym, field))
        setattr(gym, field, changes[field])
        new_values[field] = _audit_copy(changes[field])
    if new_values:
        try:
            gym.save(update_fields=[*new_values.keys(), "updated_at"])
        except IntegrityError as exc:
            raise serializers.ValidationError(
                "The gym profile conflicts with a database constraint."
            ) from exc
        write_tenant_audit(
            gym=gym,
            actor_user=actor,
            actor_membership_id=actor_membership_id,
            action="GYM_PROFILE_UPDATED",
            target_type="Gym",
            target_id=gym.id,
            request_id=request_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
            module="gym",
        )
    return gym


@transaction.atomic
def update_gym_settings(
    *,
    gym_id: uuid.UUID,
    data: dict[str, Any],
    actor: Any,
    actor_membership_id: uuid.UUID,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> GymSettings:
    """Update typed operational settings for the authenticated gym."""

    changes = prepare_settings_changes(data)
    set_gym_context(gym_id=gym_id)
    try:
        gym = _require_active_gym(gym_id)
    except Gym.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    membership = _require_active_membership(
        gym_id=gym_id, actor=actor, actor_membership_id=actor_membership_id
    )
    if not membership.is_owner:
        raise SettingsManagementForbidden()
    settings_row = GymSettings.objects.select_for_update().get(gym_id=gym_id)

    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    for field, value in changes.items():
        if getattr(settings_row, field) == value:
            continue
        old_values[field] = _audit_copy(getattr(settings_row, field))
        setattr(settings_row, field, value)
        new_values[field] = _audit_copy(value)
    if settings_row.require_separate_finance_approval is not True:
        old_values["require_separate_finance_approval"] = False
        settings_row.require_separate_finance_approval = True
        new_values["require_separate_finance_approval"] = True
    if new_values:
        settings_row.updated_by_membership_id = actor_membership_id
        try:
            settings_row.save(
                update_fields=[*new_values.keys(), "updated_by_membership_id", "updated_at"]
            )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                "The settings conflict with a database constraint."
            ) from exc
        write_tenant_audit(
            gym=gym,
            actor_user=actor,
            actor_membership_id=actor_membership_id,
            action="GYM_SETTINGS_UPDATED",
            target_type="GymSettings",
            target_id=settings_row.id,
            request_id=request_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
            module="settings",
        )
    return settings_row

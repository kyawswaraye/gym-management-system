"""Staff invitation, profile, and access lifecycle.

Invitation assignment uses the same delegation helpers as membership-role
replacement. The delegable set is the active protected MANAGER role's
permissions minus hard-reserved approvals and ``settings.manage``. General
staff commands never set ``is_owner``, assign OWNER, or manage an Owner
membership.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, connection, transaction
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.delegation import (
    RoleGrant,
    UnavailableRoleError,
    assignment_is_allowed,
    is_protected_manager_role,
    load_delegable_permission_codes,
)
from apps.tenants.exceptions import (
    InvalidInvitation,
    StaffDelegationDenied,
    StaffInvitationInvalid,
    StaffWorkflowConflict,
)
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymMembershipRole,
    Role,
    RolePermission,
    StaffInvitation,
    StaffInvitationRole,
    StaffProfile,
)
from apps.tenants.services import _hash_invitation_token
from apps.users.identity import (
    InvitedIdentityError,
    confirm_invited_profile,
    get_or_create_invited_identity,
    set_invitation_password,
)
from apps.users.models import RefreshSession

MUTABLE_PROFILE_FIELDS = (
    "staff_code",
    "display_name",
    "phone",
    "joined_date",
    "employment_title",
    "default_commission_percent",
)
STAFF_LIST_CURSOR_SALT = "staff-list"
STAFF_LIST_CURSOR_ORDERING = "display_name,id"
STAFF_LIST_CURSOR_MAX_AGE = 24 * 60 * 60
_COMMISSION_QUANTUM = Decimal("0.01")


def staff_queryset(gym: Gym) -> QuerySet[StaffProfile]:
    pending_roles = StaffInvitationRole.objects.filter(gym=gym).order_by("role_id")
    pending_invitations = (
        StaffInvitation.objects.filter(gym=gym, status=StaffInvitation.Status.PENDING)
        .order_by("created_at", "id")
        .prefetch_related(Prefetch("staffinvitationrole_set", queryset=pending_roles))
    )
    assigned_roles = GymMembershipRole.objects.filter(gym=gym).order_by("role_id")
    return (
        StaffProfile.objects.filter(
            gym=gym,
            gym_membership__gym=gym,
            gym_membership__is_owner=False,
        )
        .select_related("gym_membership__user")
        .prefetch_related(
            Prefetch("gym_membership__gymmembershiprole_set", queryset=assigned_roles),
            Prefetch("gym_membership__staffinvitation_set", queryset=pending_invitations),
        )
        .order_by("display_name", "id")
    )


def staff_list_cursor_scope(
    *,
    gym_id: uuid.UUID,
    search: str,
    profile_status: str,
    membership_status: str,
) -> dict[str, str]:
    """Bind a cursor to this route's gym, filters, and ascending name order."""

    return {
        "gym_id": str(gym_id),
        "search": search,
        "status": profile_status,
        "membership_status": membership_status,
        "ordering": STAFF_LIST_CURSOR_ORDERING,
    }


def encode_staff_list_cursor(
    *,
    display_name: str,
    staff_id: uuid.UUID,
    scope: dict[str, str],
) -> str:
    return signing.dumps(
        {"display_name": display_name, "id": str(staff_id), **scope},
        salt=STAFF_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_staff_list_cursor(token: str, *, scope: dict[str, str]) -> tuple[str, uuid.UUID]:
    try:
        cursor = signing.loads(
            token,
            salt=STAFF_LIST_CURSOR_SALT,
            max_age=STAFF_LIST_CURSOR_MAX_AGE,
        )
        if not isinstance(cursor, dict) or any(
            cursor.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        display_name = cursor["display_name"]
        if not isinstance(display_name, str):
            raise TypeError
        staff_id = uuid.UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return display_name, staff_id


def page_staff_profiles(
    queryset: QuerySet[StaffProfile],
    *,
    gym_id: uuid.UUID,
    search: str,
    profile_status: str,
    membership_status: str,
    limit: int,
    after: str,
) -> tuple[list[StaffProfile], dict[str, Any]]:
    scope = staff_list_cursor_scope(
        gym_id=gym_id,
        search=search,
        profile_status=profile_status,
        membership_status=membership_status,
    )
    if after:
        display_name, staff_id = decode_staff_list_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(display_name__gt=display_name) | Q(display_name=display_name, id__gt=staff_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_staff_list_cursor(
            display_name=last.display_name,
            staff_id=last.id,
            scope=scope,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def staff_profile_data(profile: StaffProfile) -> dict[str, object]:
    membership = profile.gym_membership
    user = membership.user
    return {
        "id": profile.id,
        "staff_code": profile.staff_code,
        "display_name": profile.display_name,
        "phone": profile.phone,
        "joined_date": profile.joined_date,
        "status": profile.status,
        "employment_title": profile.employment_title,
        "default_commission_percent": _quantize_commission(profile.default_commission_percent),
        "terminated_date": profile.terminated_date,
        "archived_at": profile.archived_at,
        "membership": {
            "gym_membership_id": membership.id,
            "user_id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "status": membership.status,
            "is_owner": membership.is_owner,
            "authz_version": membership.authz_version,
            "invited_at": membership.invited_at,
            "activated_at": membership.activated_at,
        },
        "role_ids": _assigned_role_ids(profile),
        "invited_role_ids": _pending_role_ids(profile),
    }


@transaction.atomic
def invite_staff(
    *,
    gym_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    staff_data: dict[str, Any],
    role_ids: list[uuid.UUID],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, object]:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    if gym.status != Gym.Status.ACTIVE:
        raise StaffWorkflowConflict("Staff cannot be invited to an inactive gym.")
    set_gym_context(gym_id=gym.id)
    caller = _lock_active_caller(
        gym_id=gym.id, actor=actor, actor_membership_id=actor_membership_id
    )
    caller_permissions = _membership_permission_codes(gym_id=gym.id, membership_id=caller.id)
    if "staff.manage" not in caller_permissions:
        raise PermissionDenied()
    try:
        roles = _load_assignable_roles(gym_id=gym.id, role_ids=role_ids)
    except UnavailableRoleError as exc:
        raise StaffInvitationInvalid("One or more roles are unavailable.") from exc
    _require_delegable_roles(
        gym_id=gym.id,
        caller_is_owner=caller.is_owner,
        caller_permissions=caller_permissions,
        roles=roles,
    )

    normalized_email = str(staff_data["email"]).strip().lower()
    now = timezone.now()
    StaffInvitation.objects.filter(
        gym=gym,
        normalized_email=normalized_email,
        status=StaffInvitation.Status.PENDING,
        expires_at__lte=now,
    ).update(status=StaffInvitation.Status.EXPIRED)
    if (
        StaffInvitation.objects.select_for_update()
        .filter(
            gym=gym,
            normalized_email=normalized_email,
            status=StaffInvitation.Status.PENDING,
            expires_at__gt=now,
        )
        .exists()
    ):
        raise StaffWorkflowConflict("A pending invitation already exists for this email.")

    user = _resolve_invited_user(normalized_email=normalized_email, staff_data=staff_data)
    membership = _invite_membership(
        gym=gym,
        user=user,
        actor_membership_id=caller.id,
        joined_date=staff_data["joined_date"],
        now=now,
    )
    commission = _quantize_commission(staff_data.get("default_commission_percent", Decimal("0")))
    plaintext = secrets.token_urlsafe(48)
    try:
        with transaction.atomic():
            profile = _upsert_invited_profile(
                gym=gym,
                membership=membership,
                staff_data=staff_data,
                commission=commission,
                actor_membership_id=caller.id,
            )
            invitation = StaffInvitation.objects.create(
                gym=gym,
                gym_membership=membership,
                email=user.email,
                normalized_email=normalized_email,
                token_hash=_hash_invitation_token(plaintext),
                inviter_membership_id=caller.id,
                expires_at=now + timedelta(seconds=settings.INVITATION_TTL_SECONDS),
            )
            StaffInvitationRole.objects.bulk_create(
                [
                    StaffInvitationRole(gym=gym, staff_invitation=invitation, role=role)
                    for role in roles
                ]
            )
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from exc

    acceptance_url = (
        f"{settings.APP_PUBLIC_URL.rstrip('/')}/accept-staff-invitation?token={plaintext}"
    )
    send_mail(
        subject=f"Staff invitation for {gym.name}",
        message=(
            f"You were invited to join {gym.name} as staff. Accept the invitation: {acceptance_url}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    role_id_list = [role.id for role in roles]
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action="STAFF_INVITED",
        module="staff",
        target_type="StaffInvitation",
        target_id=invitation.id,
        request_id=request_id,
        new_values={
            "email": normalized_email,
            "status": invitation.status,
            "staff_profile_id": str(profile.id),
            "gym_membership_id": str(membership.id),
            "role_ids": [str(role_id) for role_id in role_id_list],
            "staff_code": profile.staff_code,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    staff = staff_profile_data(staff_queryset(gym).get(id=profile.id))
    return {
        "id": invitation.id,
        "email": invitation.email,
        "status": invitation.status,
        "expires_at": invitation.expires_at,
        "role_ids": role_id_list,
        "membership": staff["membership"],
        "staff": staff,
    }


def _staff_invitation_bootstrap(token_hash: str) -> tuple[uuid.UUID, uuid.UUID]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT invitation_id, gym_id FROM app_staff_invitation(%s)", [token_hash])
        row = cursor.fetchone()
    if row is None:
        raise InvalidInvitation()
    return uuid.UUID(str(row[0])), uuid.UUID(str(row[1]))


@transaction.atomic
def accept_staff_invitation(
    *,
    plaintext_token: str,
    password: str,
    confirmation: dict[str, Any],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> GymMembership:
    token_hash = _hash_invitation_token(plaintext_token)
    invitation_id, gym_id = _staff_invitation_bootstrap(token_hash)
    set_gym_context(gym_id=gym_id)
    gym = Gym.objects.select_for_update().get(id=gym_id)
    try:
        invitation = (
            StaffInvitation.objects.select_for_update()
            .select_related("gym_membership__user")
            .get(id=invitation_id, gym_id=gym_id)
        )
    except StaffInvitation.DoesNotExist as exc:
        raise InvalidInvitation() from exc
    now = timezone.now()
    if (
        invitation.status != StaffInvitation.Status.PENDING
        or invitation.expires_at <= now
        or invitation.gym_membership.is_owner
    ):
        raise InvalidInvitation()
    membership = GymMembership.objects.select_for_update().get(
        id=invitation.gym_membership_id,
        gym_id=gym_id,
    )
    if membership.is_owner or membership.status != GymMembership.Status.INVITED:
        raise InvalidInvitation()
    if gym.status != Gym.Status.ACTIVE:
        raise StaffWorkflowConflict("The gym must be active before invitation acceptance.")
    try:
        profile = StaffProfile.objects.select_for_update().get(
            gym_id=gym_id,
            gym_membership=membership,
        )
    except StaffProfile.DoesNotExist as exc:
        raise InvalidInvitation() from exc
    invitation_roles = list(
        StaffInvitationRole.objects.filter(gym_id=gym_id, staff_invitation=invitation).order_by(
            "role_id"
        )
    )
    role_ids = [row.role_id for row in invitation_roles]
    locked_roles = list(
        Role.objects.select_for_update(of=("self",)).filter(gym_id=gym_id, id__in=role_ids)
    )
    if len(locked_roles) != len(role_ids) or any(
        role.key == "OWNER" or not role.is_active or role.gym_id != gym_id for role in locked_roles
    ):
        raise InvalidInvitation()
    if profile.status in {StaffProfile.Status.TERMINATED, StaffProfile.Status.ARCHIVED}:
        raise StaffWorkflowConflict("The staff profile can no longer be activated.")

    user = membership.user
    if user.id != invitation.gym_membership.user_id or not user.is_active or user.is_platform_admin:
        raise InvalidInvitation()
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
    first_name = user.first_name
    last_name = user.last_name
    phone = user.phone
    profile_changed = False
    if "first_name" in confirmation:
        first_name = str(confirmation["first_name"]).strip()
        profile_changed = True
    if "last_name" in confirmation:
        last_name = str(confirmation["last_name"]).strip()
        profile_changed = True
    if "phone" in confirmation:
        phone = str(confirmation["phone"]).strip()
        profile.phone = phone
        profile_changed = True
    if "display_name" in confirmation:
        profile.display_name = str(confirmation["display_name"]).strip()
    if profile_changed:
        try:
            confirm_invited_profile(
                token_hash=token_hash,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                changed_at=now,
            )
        except InvitedIdentityError as exc:
            raise InvalidInvitation() from exc
        user.first_name = first_name
        user.last_name = last_name
        user.phone = phone

    membership.status = GymMembership.Status.ACTIVE
    membership.is_owner = False
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
    role_ids = [row.role_id for row in invitation_roles]
    GymMembershipRole.objects.filter(gym_id=gym_id, gym_membership=membership).exclude(
        role_id__in=role_ids
    ).delete()
    GymMembershipRole.objects.bulk_create(
        [
            GymMembershipRole(
                gym_id=gym_id,
                gym_membership=membership,
                role_id=role_id,
                created_by_membership_id=invitation.inviter_membership_id,
            )
            for role_id in role_ids
        ],
        ignore_conflicts=True,
    )
    profile.status = StaffProfile.Status.ACTIVE
    profile.updated_by_membership_id = invitation.inviter_membership_id
    profile.save(
        update_fields=[
            "phone",
            "display_name",
            "status",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    invitation.status = StaffInvitation.Status.ACCEPTED
    invitation.accepted_at = now
    invitation.accepted_by_user = user
    invitation.save(update_fields=["status", "accepted_at", "accepted_by_user", "updated_at"])
    write_tenant_audit(
        gym=gym,
        actor_user=user,
        actor_membership_id=membership.id,
        action="STAFF_INVITATION_ACCEPTED",
        module="staff",
        target_type="GymMembership",
        target_id=membership.id,
        request_id=request_id,
        new_values={
            "status": membership.status,
            "is_owner": False,
            "authz_version": membership.authz_version,
            "staff_profile_id": str(profile.id),
            "role_ids": [str(role_id) for role_id in role_ids],
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    membership.user = user
    return membership


@transaction.atomic
def update_staff_profile(
    *,
    gym_id: uuid.UUID,
    staff_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> StaffProfile:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    set_gym_context(gym_id=gym.id)
    caller = _lock_active_caller(
        gym_id=gym.id, actor=actor, actor_membership_id=actor_membership_id
    )
    if "staff.manage" not in _membership_permission_codes(gym_id=gym.id, membership_id=caller.id):
        raise PermissionDenied()
    profile = _lock_manageable_profile(gym_id=gym.id, staff_id=staff_id)
    old_values: dict[str, object] = {}
    new_values: dict[str, object] = {}
    for field in MUTABLE_PROFILE_FIELDS:
        if field not in changes:
            continue
        value = _normalize_profile_value(field, changes[field])
        if getattr(profile, field) == value:
            continue
        old_values[field] = _audit_value(getattr(profile, field))
        setattr(profile, field, value)
        new_values[field] = _audit_value(value)
    if new_values:
        profile.updated_by_membership_id = caller.id
        try:
            with transaction.atomic():
                profile.save(
                    update_fields=[
                        *new_values.keys(),
                        "updated_by_membership_id",
                        "updated_at",
                    ]
                )
        except IntegrityError as exc:
            raise _conflict_from_integrity(exc) from exc
        write_tenant_audit(
            gym=gym,
            actor_user=actor,
            actor_membership_id=caller.id,
            action="STAFF_UPDATED",
            module="staff",
            target_type="StaffProfile",
            target_id=profile.id,
            request_id=request_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return staff_queryset(gym).get(id=profile.id)


@transaction.atomic
def activate_staff(
    *,
    gym_id: uuid.UUID,
    staff_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    reason: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> StaffProfile:
    return _transition_staff_access(
        gym_id=gym_id,
        staff_id=staff_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        target_status=GymMembership.Status.ACTIVE,
    )


@transaction.atomic
def suspend_staff(
    *,
    gym_id: uuid.UUID,
    staff_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    reason: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> StaffProfile:
    return _transition_staff_access(
        gym_id=gym_id,
        staff_id=staff_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        target_status=GymMembership.Status.SUSPENDED,
    )


def _transition_staff_access(
    *,
    gym_id: uuid.UUID,
    staff_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    target_status: str,
) -> StaffProfile:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    set_gym_context(gym_id=gym.id)
    caller = _lock_active_caller(
        gym_id=gym.id, actor=actor, actor_membership_id=actor_membership_id
    )
    if "staff.manage" not in _membership_permission_codes(gym_id=gym.id, membership_id=caller.id):
        raise PermissionDenied()
    profile = _lock_manageable_profile(gym_id=gym.id, staff_id=staff_id)
    membership = profile.gym_membership
    if membership.is_owner:
        raise StaffProfile.DoesNotExist()
    if profile.status in {StaffProfile.Status.TERMINATED, StaffProfile.Status.ARCHIVED}:
        raise StaffWorkflowConflict("The staff profile can no longer change access.")
    previous_status = membership.status
    if target_status == GymMembership.Status.ACTIVE:
        if previous_status != GymMembership.Status.SUSPENDED:
            raise StaffWorkflowConflict("Only a suspended staff membership can be activated.")
        action = "STAFF_ACTIVATED"
    else:
        if previous_status != GymMembership.Status.ACTIVE:
            raise StaffWorkflowConflict("Only an active staff membership can be suspended.")
        action = "STAFF_SUSPENDED"
    now = timezone.now()
    membership.status = target_status
    membership.is_owner = False
    membership.authz_version += 1
    if target_status == GymMembership.Status.ACTIVE and membership.activated_at is None:
        membership.activated_at = now
    membership.save(
        update_fields=["status", "is_owner", "authz_version", "activated_at", "updated_at"]
    )
    if target_status == GymMembership.Status.SUSPENDED:
        RefreshSession.objects.filter(
            selected_gym_id=gym.id,
            gym_membership_id=membership.id,
            revoked_at__isnull=True,
        ).update(revoked_at=now, revocation_reason="STAFF_SUSPENDED")
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action=action,
        module="staff",
        target_type="GymMembership",
        target_id=membership.id,
        request_id=request_id,
        old_values={"status": previous_status},
        new_values={
            "status": membership.status,
            "authz_version": membership.authz_version,
            "reason": reason,
            "staff_profile_id": str(profile.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return staff_queryset(gym).get(id=profile.id)


def _lock_active_caller(
    *, gym_id: uuid.UUID, actor: Any, actor_membership_id: uuid.UUID
) -> GymMembership:
    try:
        return GymMembership.objects.select_for_update().get(
            id=actor_membership_id,
            gym_id=gym_id,
            user=actor,
            status=GymMembership.Status.ACTIVE,
        )
    except GymMembership.DoesNotExist as exc:
        raise AuthenticationFailed() from exc


def _lock_manageable_profile(*, gym_id: uuid.UUID, staff_id: uuid.UUID) -> StaffProfile:
    try:
        membership = GymMembership.objects.select_for_update().get(
            gym_id=gym_id,
            is_owner=False,
            staffprofile__id=staff_id,
            staffprofile__gym_id=gym_id,
        )
    except GymMembership.DoesNotExist as exc:
        raise StaffProfile.DoesNotExist() from exc
    return (
        StaffProfile.objects.select_for_update()
        .select_related("gym_membership__user")
        .get(id=staff_id, gym_id=gym_id, gym_membership=membership)
    )


def _membership_permission_codes(*, gym_id: uuid.UUID, membership_id: uuid.UUID) -> frozenset[str]:
    return frozenset(
        RolePermission.objects.filter(
            gym_id=gym_id,
            role__gymmembershiprole__gym_membership_id=membership_id,
            role__gymmembershiprole__gym_id=gym_id,
            role__is_active=True,
            permission__is_active=True,
        ).values_list("permission__code", flat=True)
    )


def _load_assignable_roles(*, gym_id: uuid.UUID, role_ids: list[uuid.UUID]) -> list[Role]:
    unique_ids = list(dict.fromkeys(role_ids))
    roles = list(
        Role.objects.select_for_update(of=("self",))
        .select_related("source_template")
        .filter(
            gym_id=gym_id,
            id__in=unique_ids,
            is_active=True,
        )
        .order_by("id")
    )
    if len(roles) != len(unique_ids) or any(role.key == "OWNER" for role in roles):
        raise UnavailableRoleError()
    by_id = {role.id: role for role in roles}
    return [by_id[role_id] for role_id in unique_ids]


def _require_delegable_roles(
    *,
    gym_id: uuid.UUID,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    roles: list[Role],
) -> None:
    delegable = load_delegable_permission_codes(gym_id=gym_id)
    permission_map: dict[uuid.UUID, set[str]] = {role.id: set() for role in roles}
    for role_id, code in RolePermission.objects.filter(
        gym_id=gym_id,
        role_id__in=permission_map,
        role__gym_id=gym_id,
        permission__is_active=True,
    ).values_list("role_id", "permission__code"):
        permission_map[role_id].add(code)
    grants = [
        RoleGrant(
            key=role.key,
            permission_codes=frozenset(permission_map[role.id]),
            is_protected_manager=is_protected_manager_role(role),
            is_active=role.is_active,
        )
        for role in roles
    ]
    if not assignment_is_allowed(
        caller_is_owner=caller_is_owner,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable,
        roles=grants,
    ):
        raise StaffDelegationDenied()


def _resolve_invited_user(*, normalized_email: str, staff_data: dict[str, Any]) -> Any:
    user = get_or_create_invited_identity(
        email=normalized_email,
        first_name=str(staff_data["first_name"]).strip(),
        last_name=str(staff_data["last_name"]).strip(),
        phone=str(staff_data.get("phone", "")).strip(),
    )
    if not user.is_active or user.is_platform_admin:
        raise StaffWorkflowConflict("The invitation could not be issued.")
    return user


def _invite_membership(
    *,
    gym: Gym,
    user: Any,
    actor_membership_id: uuid.UUID,
    joined_date: date,
    now: datetime,
) -> GymMembership:
    membership = GymMembership.objects.select_for_update().filter(gym=gym, user=user).first()
    if membership is None:
        return GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.INVITED,
            is_owner=False,
            joined_date=joined_date,
            invited_at=now,
            created_by_membership_id=actor_membership_id,
        )
    if membership.is_owner or membership.status in {
        GymMembership.Status.ACTIVE,
        GymMembership.Status.SUSPENDED,
    }:
        raise StaffWorkflowConflict("This person already has access to the gym.")
    membership.status = GymMembership.Status.INVITED
    membership.is_owner = False
    membership.joined_date = joined_date
    membership.invited_at = now
    membership.activated_at = None
    membership.revoked_at = None
    membership.authz_version += 1
    membership.save(
        update_fields=[
            "status",
            "is_owner",
            "joined_date",
            "invited_at",
            "activated_at",
            "revoked_at",
            "authz_version",
            "updated_at",
        ]
    )
    return membership


def _upsert_invited_profile(
    *,
    gym: Gym,
    membership: GymMembership,
    staff_data: dict[str, Any],
    commission: Decimal,
    actor_membership_id: uuid.UUID,
) -> StaffProfile:
    defaults = {
        "staff_code": str(staff_data["staff_code"]).strip(),
        "display_name": str(staff_data["display_name"]).strip(),
        "phone": str(staff_data.get("phone", "")).strip(),
        "joined_date": staff_data["joined_date"],
        "status": StaffProfile.Status.ACTIVE,
        "default_commission_percent": commission,
        "employment_title": str(staff_data.get("employment_title", "")).strip(),
        "created_by_membership_id": actor_membership_id,
        "updated_by_membership_id": actor_membership_id,
    }
    profile = (
        StaffProfile.objects.select_for_update().filter(gym=gym, gym_membership=membership).first()
    )
    if profile is None:
        return StaffProfile.objects.create(gym=gym, gym_membership=membership, **defaults)
    if profile.status in {StaffProfile.Status.TERMINATED, StaffProfile.Status.ARCHIVED}:
        raise StaffWorkflowConflict("The staff profile can no longer be invited.")
    for field, value in defaults.items():
        if field != "created_by_membership_id":
            setattr(profile, field, value)
    profile.save(
        update_fields=[
            "staff_code",
            "display_name",
            "phone",
            "joined_date",
            "status",
            "default_commission_percent",
            "employment_title",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    return profile


def _assigned_role_ids(profile: StaffProfile) -> list[uuid.UUID]:
    cached = getattr(profile.gym_membership, "_prefetched_objects_cache", {}).get(
        "gymmembershiprole_set"
    )
    if cached is not None:
        return [row.role_id for row in cached]
    return list(
        GymMembershipRole.objects.filter(
            gym_id=profile.gym_id,
            gym_membership_id=profile.gym_membership_id,
        )
        .order_by("role_id")
        .values_list("role_id", flat=True)
    )


def _pending_role_ids(profile: StaffProfile) -> list[uuid.UUID]:
    cached = getattr(profile.gym_membership, "_prefetched_objects_cache", {}).get(
        "staffinvitation_set"
    )
    if cached is not None:
        role_ids: list[uuid.UUID] = []
        for invitation in cached:
            if invitation.status != StaffInvitation.Status.PENDING:
                continue
            invited = getattr(invitation, "_prefetched_objects_cache", {}).get(
                "staffinvitationrole_set"
            )
            if invited is None:
                invited = invitation.staffinvitationrole_set.all()
            role_ids.extend(row.role_id for row in invited)
        return role_ids
    return list(
        StaffInvitationRole.objects.filter(
            gym_id=profile.gym_id,
            staff_invitation__gym_membership_id=profile.gym_membership_id,
            staff_invitation__status=StaffInvitation.Status.PENDING,
        )
        .order_by("role_id")
        .values_list("role_id", flat=True)
    )


def _normalize_profile_value(field: str, value: Any) -> Any:
    if field == "default_commission_percent":
        return _quantize_commission(value)
    if field in {"staff_code", "display_name", "phone", "employment_title"}:
        return str(value).strip()
    return value


def _quantize_commission(value: Any) -> Decimal:
    return Decimal(value).quantize(_COMMISSION_QUANTUM)


def _audit_value(value: Any) -> object:
    if isinstance(value, Decimal):
        return str(_quantize_commission(value))
    if isinstance(value, date):
        return value.isoformat()
    return value


def _conflict_from_integrity(exc: IntegrityError) -> StaffWorkflowConflict:
    message = str(exc).lower()
    if "staffprof_gym_code" in message or "staff_code" in message:
        return StaffWorkflowConflict("The staff code is already in use.")
    if "invite_pending_email" in message or "normalized_email" in message:
        return StaffWorkflowConflict("A pending invitation already exists for this email.")
    return StaffWorkflowConflict("The staff record conflicts with an existing record.")

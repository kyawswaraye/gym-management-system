"""Tenant-scoped trainer and assignment reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import models
from django.db.models import (
    Case,
    CharField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.members.models import Member, Membership
from apps.tenants.models import Gym, GymMembership, Role, StaffProfile
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServicePackage,
    ServiceSessionLedger,
    TrainerAssignment,
)

_TRAINER_SALT = "trainer-list"
_SERVICE_PACKAGE_SALT = "service-package-list"
_PT_SALE_SALT = "pt-sale-list"
_MEMBER_PACKAGE_SALT = "member-package-list"
_LEDGER_SALT = "member-package-ledger"
_PT_COMMISSION_SALT = "pt-commission-list"
_PT_BOOKING_SALT = "pt.booking.v1"
_CALENDAR_DAYS = 42
_RECEPTION_ROLE = "RECEPTIONIST"
_ASSIGNMENT_SALT = "trainer-assignment-list"
_ASSIGNED_MEMBER_SALT = "trainer-assigned-member-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_TRAINER_ROLE = "TRAINER"
_OWNER_ROLE = "OWNER"
_MANAGER_ROLE = "MANAGER"


@dataclass(frozen=True)
class AssignmentScope:
    """Trainer callers are limited to their own active staff profile.

    Owner and manager memberships follow the granted permission across the gym.
    ``restricted`` with a null profile matches no rows.
    """

    restricted: bool
    trainer_staff_profile_id: UUID | None


def holds_protected_role(*, gym_id: UUID, membership_id: UUID, role_key: str) -> bool:
    return Role.objects.filter(
        gym_id=gym_id,
        is_active=True,
        key=role_key,
        source_template__key=role_key,
        gymmembershiprole__gym_id=gym_id,
        gymmembershiprole__gym_membership_id=membership_id,
    ).exists()


def holds_protected_management_role(*, gym_id: UUID, membership_id: UUID) -> bool:
    """Protected OWNER or MANAGER removes trainer assignment limits.

    A custom role, including one whose key is not TRAINER, does not.
    """

    return holds_protected_role(
        gym_id=gym_id, membership_id=membership_id, role_key=_OWNER_ROLE
    ) or holds_protected_role(gym_id=gym_id, membership_id=membership_id, role_key=_MANAGER_ROLE)


def protected_trainer_assignment_restricted(*, gym_id: UUID, actor: GymMembership) -> bool:
    """Scope a protected TRAINER unless that membership also holds management.

    The same decision covers assignment reads and generic member, membership,
    check-in, and profile-photo routes.
    """

    if holds_protected_management_role(gym_id=gym_id, membership_id=actor.id):
        return False
    return holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key=_TRAINER_ROLE)


def assignment_scope(*, gym_id: UUID, actor: GymMembership) -> AssignmentScope:
    if not protected_trainer_assignment_restricted(gym_id=gym_id, actor=actor):
        return AssignmentScope(restricted=False, trainer_staff_profile_id=None)
    profile_id = (
        StaffProfile.objects.filter(
            gym_id=gym_id,
            gym_membership_id=actor.id,
            status=StaffProfile.Status.ACTIVE,
        )
        .values_list("id", flat=True)
        .first()
    )
    return AssignmentScope(restricted=True, trainer_staff_profile_id=profile_id)


@dataclass(frozen=True)
class TrainerMemberScope:
    """Assignment scope for a protected TRAINER membership.

    Protected OWNER or MANAGER on that membership keeps the caller's granted
    gym-wide scope. Another custom role does not. A restricted membership with
    no active staff profile matches no members.
    """

    restricted: bool
    profile_ids: tuple[UUID, ...]


def gym_local_date(gym: Gym) -> date:
    return timezone.localdate(timezone=ZoneInfo(gym.timezone))


def trainer_member_scope(*, gym_id: UUID, actor: GymMembership) -> TrainerMemberScope:
    """Use the same protected-role decision as assignment reads."""

    if not protected_trainer_assignment_restricted(gym_id=gym_id, actor=actor):
        return TrainerMemberScope(restricted=False, profile_ids=())
    profile_ids = tuple(
        StaffProfile.objects.filter(
            gym_id=gym_id,
            gym_membership_id=actor.id,
            status=StaffProfile.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    return TrainerMemberScope(restricted=True, profile_ids=profile_ids)


def deny_trainer_only(*, gym_id: UUID, actor: GymMembership) -> None:
    """Close trainer-only routes that stay shut even when a member is assigned."""

    if trainer_member_scope(gym_id=gym_id, actor=actor).restricted:
        raise Http404


def require_own_active_trainer_profile_id(*, gym_id: UUID, actor: GymMembership) -> UUID:
    """Return this caller's active staff profile when protected TRAINER is held.

    Protected OWNER or MANAGER on the same membership does not replace TRAINER
    and does not select another profile. A membership without that protected
    role, or without an active same-gym profile for this membership, is not found.
    """

    if actor.status != GymMembership.Status.ACTIVE or not holds_protected_role(
        gym_id=gym_id, membership_id=actor.id, role_key=_TRAINER_ROLE
    ):
        raise Http404
    profile_id = (
        StaffProfile.objects.filter(
            gym_id=gym_id,
            gym_membership_id=actor.id,
            status=StaffProfile.Status.ACTIVE,
        )
        .values_list("id", flat=True)
        .first()
    )
    if not isinstance(profile_id, UUID):
        raise Http404
    return profile_id


def _effective_assignments(
    *,
    gym_id: UUID,
    profile_ids: tuple[UUID, ...],
    as_of: date,
) -> QuerySet[TrainerAssignment]:
    return TrainerAssignment.objects.filter(
        gym_id=gym_id,
        trainer_staff_profile_id__in=profile_ids,
        status=TrainerAssignment.Status.ACTIVE,
        effective_from__lte=as_of,
    ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=as_of))


def member_is_assigned(
    *,
    gym_id: UUID,
    profile_ids: tuple[UUID, ...],
    member_id: UUID,
    as_of: date,
) -> bool:
    if not profile_ids:
        return False
    return (
        _effective_assignments(
            gym_id=gym_id,
            profile_ids=profile_ids,
            as_of=as_of,
        )
        .filter(member_id=member_id)
        .exists()
    )


def require_assigned_member(
    *,
    gym_id: UUID,
    actor: GymMembership,
    member_id: UUID,
    as_of: date,
) -> None:
    """404 when a trainer-only caller has no effective assignment for this member."""

    scope = trainer_member_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return
    if not member_is_assigned(
        gym_id=gym_id,
        profile_ids=scope.profile_ids,
        member_id=member_id,
        as_of=as_of,
    ):
        raise Http404


def scope_to_assigned_members[RowT: models.Model](
    queryset: QuerySet[RowT],
    *,
    gym_id: UUID,
    actor: GymMembership,
    as_of: date,
    member_ref: str,
) -> QuerySet[RowT]:
    """Keep a trainer-only page inside one correlated assignment probe."""

    scope = trainer_member_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return queryset
    if not scope.profile_ids:
        return queryset.none()
    assigned = _effective_assignments(
        gym_id=gym_id,
        profile_ids=scope.profile_ids,
        as_of=as_of,
    ).filter(member_id=OuterRef(member_ref))
    return queryset.filter(Exists(assigned))


def require_visible_membership(
    *,
    gym: Gym,
    actor: GymMembership,
    membership_id: UUID,
) -> None:
    member_id = (
        Membership.objects.filter(gym=gym, id=membership_id)
        .values_list("member_id", flat=True)
        .first()
    )
    if member_id is None:
        raise Http404
    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member_id,
        as_of=gym_local_date(gym),
    )


def active_trainer_queryset(*, gym_id: UUID) -> QuerySet[StaffProfile]:
    return StaffProfile.objects.filter(
        gym_id=gym_id,
        status=StaffProfile.Status.ACTIVE,
        gym_membership__gym_id=gym_id,
        gym_membership__status=GymMembership.Status.ACTIVE,
        gym_membership__gymmembershiprole__gym_id=gym_id,
        gym_membership__gymmembershiprole__role__gym_id=gym_id,
        gym_membership__gymmembershiprole__role__is_active=True,
        gym_membership__gymmembershiprole__role__key=_TRAINER_ROLE,
        gym_membership__gymmembershiprole__role__source_template__key=_TRAINER_ROLE,
    ).distinct()


def visible_trainers(*, gym_id: UUID, actor: GymMembership) -> QuerySet[StaffProfile]:
    queryset = active_trainer_queryset(gym_id=gym_id)
    scope = assignment_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return queryset
    if scope.trainer_staff_profile_id is None:
        return queryset.none()
    return queryset.filter(id=scope.trainer_staff_profile_id)


def visible_assignments(*, gym_id: UUID, actor: GymMembership) -> QuerySet[TrainerAssignment]:
    queryset = TrainerAssignment.objects.filter(gym_id=gym_id)
    scope = assignment_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return queryset
    if scope.trainer_staff_profile_id is None:
        return queryset.none()
    return queryset.filter(trainer_staff_profile_id=scope.trainer_staff_profile_id)


def member_pt_client_badge(*, gym_id: UUID, as_of: date) -> Exists:
    return Exists(
        TrainerAssignment.objects.filter(
            gym_id=gym_id,
            member_id=OuterRef("pk"),
            status=TrainerAssignment.Status.ACTIVE,
            effective_from__lte=as_of,
        ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=as_of))
    )


def annotate_member_pt_client(
    queryset: QuerySet[Member], *, gym_id: UUID, as_of: date
) -> QuerySet[Member]:
    return queryset.annotate(pt_client_badge=member_pt_client_badge(gym_id=gym_id, as_of=as_of))


def member_has_active_assignment(member: Member, *, as_of: date) -> bool:
    return (
        TrainerAssignment.objects.filter(
            gym_id=member.gym_id,
            member_id=member.id,
            status=TrainerAssignment.Status.ACTIVE,
            effective_from__lte=as_of,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=as_of))
        .exists()
    )


def effective_assignment_filter(*, as_of: date) -> Q:
    return Q(status=TrainerAssignment.Status.ACTIVE, effective_from__lte=as_of) & (
        Q(effective_until__isnull=True) | Q(effective_until__gte=as_of)
    )


def get_visible_trainer(*, gym_id: UUID, actor: GymMembership, trainer_id: UUID) -> StaffProfile:
    trainer = visible_trainers(gym_id=gym_id, actor=actor).filter(id=trainer_id).first()
    if trainer is None:
        raise Http404
    return trainer


def get_visible_assignment(
    *, gym_id: UUID, actor: GymMembership, assignment_id: UUID
) -> TrainerAssignment:
    assignment = (
        visible_assignments(gym_id=gym_id, actor=actor)
        .select_related("trainer_staff_profile", "member")
        .filter(id=assignment_id)
        .first()
    )
    if assignment is None:
        raise Http404
    return assignment


def _scope_binding(scope: AssignmentScope) -> dict[str, str]:
    profile_id = scope.trainer_staff_profile_id
    return {
        "restricted": "1" if scope.restricted else "0",
        "trainer_staff_profile_id": "" if profile_id is None else str(profile_id),
    }


def _load_cursor(
    token: str,
    *,
    salt: str,
    scope: dict[str, str],
    position: set[str],
) -> dict[str, Any]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict) or set(loaded) != set(scope) | position:
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return loaded


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    return value


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    if parsed.tzinfo is None:
        raise ValidationError({"after": ["Invalid cursor."]})
    return parsed


def page_trainers(
    *,
    gym_id: UUID,
    actor: GymMembership,
    limit: int,
    after: str,
) -> tuple[list[StaffProfile], dict[str, Any]]:
    scope = assignment_scope(gym_id=gym_id, actor=actor)
    cursor_scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor.id),
        "ordering": "display_name,id",
        **_scope_binding(scope),
    }
    if after:
        _load_cursor(after, salt=_TRAINER_SALT, scope=cursor_scope, position={"display_name", "id"})
    queryset = visible_trainers(gym_id=gym_id, actor=actor).order_by("display_name", "id")
    if after:
        loaded = _load_cursor(
            after, salt=_TRAINER_SALT, scope=cursor_scope, position={"display_name", "id"}
        )
        display_name = _text(loaded["display_name"])
        profile_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(display_name__gt=display_name) | Q(display_name=display_name, id__gt=profile_id)
        )
    return _page(
        list(queryset[: limit + 1]),
        limit=limit,
        salt=_TRAINER_SALT,
        scope=cursor_scope,
        position=lambda row: {"display_name": row.display_name, "id": str(row.id)},
    )


def page_assignments(
    *,
    gym_id: UUID,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    member_id: UUID | None,
    limit: int,
    after: str,
) -> tuple[list[TrainerAssignment], dict[str, Any]]:
    scope = assignment_scope(gym_id=gym_id, actor=actor)
    cursor_scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor.id),
        "status": status,
        "trainer_filter": "" if trainer_staff_profile_id is None else str(trainer_staff_profile_id),
        "member_filter": "" if member_id is None else str(member_id),
        "ordering": "created_at DESC,id DESC",
        **_scope_binding(scope),
    }
    position = {"created_at", "id"}
    if after:
        _load_cursor(after, salt=_ASSIGNMENT_SALT, scope=cursor_scope, position=position)
    queryset = visible_assignments(gym_id=gym_id, actor=actor).select_related(
        "trainer_staff_profile", "member"
    )
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    queryset = queryset.order_by("-created_at", "-id")
    if after:
        loaded = _load_cursor(after, salt=_ASSIGNMENT_SALT, scope=cursor_scope, position=position)
        created_at = _instant(loaded["created_at"])
        assignment_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=assignment_id)
        )
    return _page(
        list(queryset[: limit + 1]),
        limit=limit,
        salt=_ASSIGNMENT_SALT,
        scope=cursor_scope,
        position=lambda row: {"created_at": row.created_at.isoformat(), "id": str(row.id)},
    )


def page_assigned_members(
    *,
    gym_id: UUID,
    actor: GymMembership,
    trainer_id: UUID,
    as_of: date,
    limit: int,
    after: str,
) -> tuple[list[TrainerAssignment], dict[str, Any]]:
    trainer = get_visible_trainer(gym_id=gym_id, actor=actor, trainer_id=trainer_id)
    scope = assignment_scope(gym_id=gym_id, actor=actor)
    cursor_scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor.id),
        "trainer_id": str(trainer.id),
        "as_of": as_of.isoformat(),
        "ordering": "normalized_full_name,member_id",
        **_scope_binding(scope),
    }
    position = {"normalized_full_name", "member_id"}
    if after:
        _load_cursor(after, salt=_ASSIGNED_MEMBER_SALT, scope=cursor_scope, position=position)
    queryset = (
        TrainerAssignment.objects.filter(gym_id=gym_id, trainer_staff_profile_id=trainer.id)
        .filter(effective_assignment_filter(as_of=as_of))
        .select_related("member")
        .order_by("member__normalized_full_name", "member_id")
    )
    if after:
        loaded = _load_cursor(
            after, salt=_ASSIGNED_MEMBER_SALT, scope=cursor_scope, position=position
        )
        name = _text(loaded["normalized_full_name"])
        member_id = _uuid(loaded["member_id"])
        queryset = queryset.filter(
            Q(member__normalized_full_name__gt=name)
            | Q(member__normalized_full_name=name, member_id__gt=member_id)
        )
    return _page(
        list(queryset[: limit + 1]),
        limit=limit,
        salt=_ASSIGNED_MEMBER_SALT,
        scope=cursor_scope,
        position=lambda row: {
            "normalized_full_name": row.member.normalized_full_name,
            "member_id": str(row.member_id),
        },
    )


def page_service_packages(
    *,
    gym_id: UUID,
    actor: GymMembership,
    package_type: str,
    is_active: bool | None,
    limit: int,
    after: str,
) -> tuple[list[ServicePackage], dict[str, Any]]:
    """Return one gym's package definitions in code, id order."""

    filters = Q(gym_id=gym_id)
    if package_type:
        filters &= Q(type=package_type)
    if is_active is not None:
        filters &= Q(is_active=is_active)
    scope = {
        "gym_id": str(gym_id),
        "actor_id": str(actor.id),
        "type": package_type,
        "is_active": "" if is_active is None else str(is_active).lower(),
    }
    queryset = ServicePackage.objects.filter(filters).order_by("code", "id")
    if after:
        loaded = _load_cursor(
            after,
            salt=_SERVICE_PACKAGE_SALT,
            scope=scope,
            position={"code", "id"},
        )
        code = _text(loaded["code"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(Q(code__gt=code) | Q(code=code, id__gt=row_id))
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_SERVICE_PACKAGE_SALT,
        scope=scope,
        position=lambda row: {"code": row.code, "id": str(row.id)},
    )


def pt_sale_visibility(
    *,
    gym: Gym,
    actor: GymMembership,
) -> tuple[QuerySet[PTSale], dict[str, str]]:
    """Return draft-sale rows the caller may see, plus the cursor scope binding.

    A protected receptionist sees only sales that membership created. A protected
    trainer without protected owner or manager sees only that caller's active
    profiles whose member assignment is effective today. Both limits apply
    together when the membership holds both roles.
    """

    reception = holds_protected_role(
        gym_id=gym.id, membership_id=actor.id, role_key=_RECEPTION_ROLE
    )
    trainer_scope = trainer_member_scope(gym_id=gym.id, actor=actor)
    granted = MemberServicePackage.objects.filter(
        gym_id=OuterRef("gym_id"),
        pt_sale_id=OuterRef("id"),
    ).values("id")[:1]
    queryset = (
        PTSale.objects.select_related("payment", "payment__member")
        .filter(gym_id=gym.id)
        .annotate(granted_package_id=Subquery(granted))
    )
    if reception:
        queryset = queryset.filter(created_by_membership_id=actor.id)
    profile_key = ""
    if trainer_scope.restricted:
        profile_key = ",".join(str(item) for item in sorted(trainer_scope.profile_ids))
        if not trainer_scope.profile_ids:
            queryset = queryset.none()
        else:
            assigned = _effective_assignments(
                gym_id=gym.id,
                profile_ids=trainer_scope.profile_ids,
                as_of=gym_local_date(gym),
            ).filter(
                member_id=OuterRef("member_id"),
                trainer_staff_profile_id=OuterRef("trainer_staff_profile_id"),
            )
            queryset = queryset.filter(
                trainer_staff_profile_id__in=trainer_scope.profile_ids
            ).filter(Exists(assigned))
    return queryset, {
        "reception": "1" if reception else "0",
        "trainer_restricted": "1" if trainer_scope.restricted else "0",
        "trainer_profiles": profile_key,
    }


def get_visible_pt_sale(*, gym: Gym, actor: GymMembership, sale_id: UUID) -> PTSale:
    queryset, _binding = pt_sale_visibility(gym=gym, actor=actor)
    row = queryset.filter(id=sale_id).first()
    if row is None:
        raise Http404
    return row


def page_pt_sales(
    *,
    gym: Gym,
    actor: GymMembership,
    status: str,
    member_id: UUID | None,
    trainer_staff_profile_id: UUID | None,
    service_date_from: date | None,
    service_date_to: date | None,
    limit: int,
    after: str,
) -> tuple[list[PTSale], dict[str, Any]]:
    queryset, binding = pt_sale_visibility(gym=gym, actor=actor)
    scope = {
        "gym_id": str(gym.id),
        "actor_id": str(actor.id),
        "status": status,
        "member_id": "" if member_id is None else str(member_id),
        "trainer_staff_profile_id": (
            "" if trainer_staff_profile_id is None else str(trainer_staff_profile_id)
        ),
        "service_date_from": "" if service_date_from is None else service_date_from.isoformat(),
        "service_date_to": "" if service_date_to is None else service_date_to.isoformat(),
        "ordering": "created_at DESC,id DESC",
        **binding,
    }
    if status:
        queryset = queryset.filter(status=status)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if service_date_from is not None:
        queryset = queryset.filter(service_date__gte=service_date_from)
    if service_date_to is not None:
        queryset = queryset.filter(service_date__lte=service_date_to)
    queryset = queryset.order_by("-created_at", "-id")
    if after:
        loaded = _load_cursor(
            after,
            salt=_PT_SALE_SALT,
            scope=scope,
            position={"created_at", "id"},
        )
        created_at = _instant(loaded["created_at"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_PT_SALE_SALT,
        scope=scope,
        position=lambda row: {"created_at": row.created_at.isoformat(), "id": str(row.id)},
    )


def member_package_visibility(
    *,
    gym: Gym,
    actor: GymMembership,
) -> tuple[QuerySet[MemberServicePackage], dict[str, str]]:
    """Return grants the caller may see.

    A protected trainer without protected owner or manager sees grants only for
    members assigned to that caller today. Owner, manager, accountant, and
    receptionist follow the granted permission across the gym.
    """

    trainer_scope = trainer_member_scope(gym_id=gym.id, actor=actor)
    queryset = _annotate_packages(MemberServicePackage.objects.filter(gym_id=gym.id), gym=gym)
    profile_key = ""
    if trainer_scope.restricted:
        profile_key = ",".join(str(item) for item in sorted(trainer_scope.profile_ids))
        if not trainer_scope.profile_ids:
            queryset = queryset.none()
        else:
            assigned = _effective_assignments(
                gym_id=gym.id,
                profile_ids=trainer_scope.profile_ids,
                as_of=gym_local_date(gym),
            ).filter(member_id=OuterRef("member_id"))
            queryset = queryset.filter(Exists(assigned))
    return queryset, {
        "trainer_restricted": "1" if trainer_scope.restricted else "0",
        "trainer_profiles": profile_key,
    }


def page_member_packages(
    *,
    gym: Gym,
    actor: GymMembership,
    status: str,
    member_id: UUID | None,
    package_type: str,
    limit: int,
    after: str,
) -> tuple[list[MemberServicePackage], dict[str, Any]]:
    queryset, binding = member_package_visibility(gym=gym, actor=actor)
    scope = {
        "gym_id": str(gym.id),
        "actor_id": str(actor.id),
        "status": status,
        "member_id": "" if member_id is None else str(member_id),
        "package_type": package_type,
        "ordering": "created_at DESC,id DESC",
        **binding,
    }
    if status:
        queryset = queryset.filter(effective_status=status)  # type: ignore[misc]
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if package_type:
        queryset = queryset.filter(package_type=package_type)
    queryset = queryset.order_by("-created_at", "-id")
    if after:
        loaded = _load_cursor(
            after,
            salt=_MEMBER_PACKAGE_SALT,
            scope=scope,
            position={"created_at", "id"},
        )
        created_at = _instant(loaded["created_at"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_MEMBER_PACKAGE_SALT,
        scope=scope,
        position=lambda row: {"created_at": row.created_at.isoformat(), "id": str(row.id)},
    )


def get_visible_member_package(
    *,
    gym: Gym,
    actor: GymMembership,
    package_id: UUID,
) -> MemberServicePackage:
    queryset, _binding = member_package_visibility(gym=gym, actor=actor)
    row = queryset.filter(id=package_id).first()
    if row is None:
        raise Http404
    return row


def lock_visible_member_package(
    *,
    gym: Gym,
    actor: GymMembership,
    package_id: UUID,
    manage: bool,
) -> MemberServicePackage:
    if manage:
        row = (
            MemberServicePackage.objects.select_for_update()
            .filter(gym_id=gym.id, id=package_id)
            .first()
        )
    else:
        queryset, _binding = member_package_visibility(gym=gym, actor=actor)
        row = queryset.select_for_update(of=("self",)).filter(id=package_id).first()
    if row is None:
        raise Http404
    return row


def page_package_ledger(
    *,
    gym: Gym,
    actor: GymMembership,
    package_id: UUID,
    limit: int,
    after: str,
) -> tuple[MemberServicePackage, list[ServiceSessionLedger], dict[str, Any]]:
    package = get_visible_member_package(gym=gym, actor=actor, package_id=package_id)
    scope = {
        "gym_id": str(gym.id),
        "actor_id": str(actor.id),
        "package_id": str(package.id),
        "ordering": "occurred_at DESC,id DESC",
    }
    queryset = package.ledger_entries.all().order_by("-occurred_at", "-id")
    if after:
        loaded = _load_cursor(
            after,
            salt=_LEDGER_SALT,
            scope=scope,
            position={"occurred_at", "id"},
        )
        occurred_at = _instant(loaded["occurred_at"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(occurred_at__lt=occurred_at) | Q(occurred_at=occurred_at, id__lt=row_id)
        )
    rows = list(queryset[: limit + 1])
    page, meta = _page(
        rows,
        limit=limit,
        salt=_LEDGER_SALT,
        scope=scope,
        position=lambda row: {"occurred_at": row.occurred_at.isoformat(), "id": str(row.id)},
    )
    return package, page, meta


def _annotate_packages(
    queryset: QuerySet[MemberServicePackage],
    *,
    gym: Gym,
) -> QuerySet[MemberServicePackage]:
    today = gym_local_date(gym)
    remaining = Coalesce(
        Sum("ledger_entries__session_delta"),
        Value(0),
        output_field=IntegerField(),
    )
    return queryset.annotate(remaining_sessions=remaining).annotate(
        effective_status=Case(
            When(status=MemberServicePackage.Status.VOID, then=Value("VOID")),
            When(remaining_sessions__lte=0, then=Value("EXHAUSTED")),
            When(valid_until__lt=today, then=Value("EXPIRED")),
            default=Value("ACTIVE"),
            output_field=CharField(),
        )
    )


def commission_visibility(
    *,
    gym: Gym,
    actor: GymMembership,
) -> tuple[QuerySet[PTCommission], dict[str, str]]:
    """Return commission rows the caller may see, plus the cursor scope binding.

    A protected trainer without protected owner or manager sees only commissions
    for that caller's active trainer profiles. Owner, manager, and accountant
    follow the granted permission across the gym.
    """

    trainer_scope = trainer_member_scope(gym_id=gym.id, actor=actor)
    queryset = PTCommission.objects.filter(gym_id=gym.id)
    profile_key = ""
    if trainer_scope.restricted:
        profile_key = ",".join(str(item) for item in sorted(trainer_scope.profile_ids))
        if not trainer_scope.profile_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(trainer_staff_profile_id__in=trainer_scope.profile_ids)
    return queryset, {
        "trainer_restricted": "1" if trainer_scope.restricted else "0",
        "trainer_profiles": profile_key,
    }


def get_visible_commission(
    *,
    gym: Gym,
    actor: GymMembership,
    commission_id: UUID,
) -> PTCommission:
    queryset, _binding = commission_visibility(gym=gym, actor=actor)
    row = queryset.filter(id=commission_id).first()
    if row is None:
        raise Http404
    return row


def commission_payments(row: PTCommission) -> list[PTCommissionPayment]:
    return list(
        PTCommissionPayment.objects.filter(
            gym_id=row.gym_id,
            pt_commission_id=row.id,
        ).order_by("created_at", "id")
    )


def page_pt_commissions(
    *,
    gym: Gym,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    service_date_from: date | None,
    service_date_to: date | None,
    limit: int,
    after: str,
) -> tuple[list[PTCommission], dict[str, Any]]:
    queryset, binding = commission_visibility(gym=gym, actor=actor)
    scope = {
        "gym_id": str(gym.id),
        "actor_id": str(actor.id),
        "status": status,
        "trainer_staff_profile_id": (
            "" if trainer_staff_profile_id is None else str(trainer_staff_profile_id)
        ),
        "service_date_from": "" if service_date_from is None else service_date_from.isoformat(),
        "service_date_to": "" if service_date_to is None else service_date_to.isoformat(),
        "ordering": "created_at DESC,id DESC",
        **binding,
    }
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if service_date_from is not None:
        queryset = queryset.filter(pt_sale__service_date__gte=service_date_from)
    if service_date_to is not None:
        queryset = queryset.filter(pt_sale__service_date__lte=service_date_to)
    queryset = queryset.order_by("-created_at", "-id")
    if after:
        loaded = _load_cursor(
            after,
            salt=_PT_COMMISSION_SALT,
            scope=scope,
            position={"created_at", "id"},
        )
        created_at = _instant(loaded["created_at"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_PT_COMMISSION_SALT,
        scope=scope,
        position=lambda row: {"created_at": row.created_at.isoformat(), "id": str(row.id)},
    )


def _page(
    rows: list[Any],
    *,
    limit: int,
    salt: str,
    scope: dict[str, str],
    position: Any,
) -> tuple[list[Any], dict[str, Any]]:
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        next_after = signing.dumps({**position(page[-1]), **scope}, salt=salt, compress=True)
    return page, {"has_more": has_more, "next_after": next_after}


def page_pt_bookings(
    *,
    gym: Gym,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    member_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
    view: str,
    limit: int,
    after: str,
) -> tuple[list[PTBooking], dict[str, Any]]:
    _assert_calendar(view=view, date_from=date_from, date_to=date_to)
    scope_rows = assignment_scope(gym_id=gym.id, actor=actor)
    if (
        scope_rows.restricted
        and trainer_staff_profile_id is not None
        and trainer_staff_profile_id != scope_rows.trainer_staff_profile_id
    ):
        return [], {"has_more": False, "next_after": None}
    queryset = PTBooking.objects.filter(gym_id=gym.id)
    if scope_rows.restricted:
        if scope_rows.trainer_staff_profile_id is None:
            return [], {"has_more": False, "next_after": None}
        queryset = queryset.filter(trainer_staff_profile_id=scope_rows.trainer_staff_profile_id)
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    zone = ZoneInfo(gym.timezone)
    if date_from is not None:
        queryset = queryset.filter(
            starts_at__gte=datetime.combine(date_from, datetime.min.time(), zone)
        )
    if date_to is not None:
        next_day = date_to.fromordinal(date_to.toordinal() + 1)
        queryset = queryset.filter(
            starts_at__lt=datetime.combine(next_day, datetime.min.time(), zone)
        )
    scope = {
        "gym_id": str(gym.id),
        "actor_id": str(actor.id),
        "status": status,
        "trainer_staff_profile_id": ""
        if trainer_staff_profile_id is None
        else str(trainer_staff_profile_id),
        "member_id": "" if member_id is None else str(member_id),
        "date_from": "" if date_from is None else date_from.isoformat(),
        "date_to": "" if date_to is None else date_to.isoformat(),
        "view": view,
        "ordering": "starts_at,id",
    }
    queryset = queryset.order_by("starts_at", "id")
    if after:
        loaded = _load_cursor(
            after, salt=_PT_BOOKING_SALT, scope=scope, position={"starts_at", "id"}
        )
        starts_at = _instant(loaded["starts_at"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(starts_at__gt=starts_at) | Q(starts_at=starts_at, id__gt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_PT_BOOKING_SALT,
        scope=scope,
        position=lambda row: {"starts_at": row.starts_at.isoformat(), "id": str(row.id)},
    )


def get_pt_booking(*, gym: Gym, actor: GymMembership, booking_id: UUID) -> PTBooking:
    row = PTBooking.objects.filter(gym_id=gym.id, id=booking_id).first()
    if row is None:
        raise Http404
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if not scope.restricted:
        return row
    if scope.trainer_staff_profile_id != row.trainer_staff_profile_id:
        raise Http404
    local_day = gym_local_date(gym)
    assigned = (
        TrainerAssignment.objects.filter(
            gym_id=gym.id,
            trainer_staff_profile_id=scope.trainer_staff_profile_id,
            member_id=row.member_id,
            status=TrainerAssignment.Status.ACTIVE,
            effective_from__lte=local_day,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=local_day))
        .exists()
    )
    if not assigned:
        raise Http404
    return row


def _assert_calendar(*, view: str, date_from: date | None, date_to: date | None) -> None:
    if view != "calendar":
        return
    if date_from is None or date_to is None or date_to < date_from:
        raise ValidationError({"date_to": ["Choose a calendar range."]})
    if (date_to - date_from).days + 1 > _CALENDAR_DAYS:
        raise ValidationError({"date_to": ["The calendar range is at most 42 days."]})

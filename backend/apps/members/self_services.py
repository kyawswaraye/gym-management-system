"""Member-self reads and profile updates.

Gym and member come from the authenticated member principal. These adapters do
not accept a staff actor and do not call staff assignment gates.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from apps.audit.services import write_tenant_audit
from apps.classes.exceptions import ScheduleTooLarge
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.common.db.tenant_context import set_gym_context
from apps.members.checkin_services import member_check_in_summary, page_self_check_ins
from apps.members.models import CheckIn, Member, Membership, Payment
from apps.members.services import (
    _prepare_member_changes,
    _validate_member_dates,
    current_membership_for_summary,
    gym_today,
    inclusive_days_remaining,
    member_payment_summary,
    membership_history_for_summary,
    page_member_payments,
)
from apps.tenants.models import Gym, GymFeatureEntitlement
from apps.trainers.models import MemberServicePackage, PTBooking
from apps.trainers.selectors import _annotate_packages, annotate_member_pt_client

_SELF_FIELDS = (
    "phone",
    "email",
    "gender",
    "date_of_birth",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
)
_PACKAGE_SALT = "member-self-service-packages"
_PACKAGE_MAX_AGE = 24 * 60 * 60
_SCHEDULE_OCCURRENCE_LIMIT = 400


def load_self_member(*, gym: Gym, member_id: UUID) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = annotate_member_pt_client(
            Member.objects.filter(gym=gym, id=member_id),
            gym_id=gym.id,
            as_of=gym_today(gym),
        ).first()
    if member is None:
        raise AuthenticationFailed()
    return member


def update_self_profile(
    *,
    gym: Gym,
    member_id: UUID,
    actor_user: Any,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Member:
    narrowed = {field: changes[field] for field in _SELF_FIELDS if field in changes}
    if not narrowed:
        raise ValidationError(
            {"non_field_errors": ["This request must change at least one field."]}
        )
    prepared = _prepare_member_changes(narrowed)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        locked = Member.objects.select_for_update().filter(gym=gym, id=member_id).first()
        if locked is None or locked.status != Member.Status.ACTIVE:
            raise AuthenticationFailed()
        _validate_member_dates(
            prepared["date_of_birth"] if "date_of_birth" in prepared else locked.date_of_birth,
            locked.join_date,
        )
        applied = {
            field: value for field, value in prepared.items() if getattr(locked, field) != value
        }
        if not applied:
            return annotate_member_pt_client(
                Member.objects.filter(pk=locked.pk),
                gym_id=gym.id,
                as_of=gym_today(gym),
            ).get()
        before = {field: _audit_value(getattr(locked, field)) for field in applied}
        after = {field: _audit_value(value) for field, value in applied.items()}
        for field, value in applied.items():
            setattr(locked, field, value)
        locked.save(update_fields=[*applied.keys(), "updated_at"])
        write_tenant_audit(
            gym=gym,
            actor_user=actor_user,
            actor_membership_id=None,
            action="MEMBER_PROFILE_UPDATED",
            module="members",
            target_type="Member",
            target_id=locked.id,
            request_id=request_id,
            old_values=before,
            new_values=after,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return annotate_member_pt_client(
            Member.objects.filter(pk=locked.pk),
            gym_id=gym.id,
            as_of=gym_today(gym),
        ).get()


def member_self_dashboard(*, gym: Gym, member: Member) -> dict[str, object]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        today = gym_today(gym)
        current = current_membership_for_summary(gym=gym, member=member, today=today)
        history, has_more = membership_history_for_summary(gym=gym, member=member)
        days_remaining = (
            inclusive_days_remaining(current.expiry_date, today) if current is not None else None
        )
        if _feature_enabled(gym, "finance"):
            payment_summary: dict[str, object] = dict(
                member_payment_summary(gym=gym, member=member)
            )
        else:
            payment_summary = {
                "available": False,
                "currency": gym.currency,
                "total_charged": None,
                "total_refunded": None,
                "net_paid": None,
                "outstanding_membership_balance": None,
            }
        annotated = annotate_member_pt_client(
            Member.objects.filter(pk=member.pk),
            gym_id=gym.id,
            as_of=today,
        ).get()
        return {
            "member": annotated,
            "current_membership": current,
            "days_remaining": days_remaining,
            "membership_history": history,
            "membership_history_has_more": has_more,
            "payment_summary": payment_summary,
            "check_in_summary": member_check_in_summary(gym=gym, member=member),
        }


def member_self_membership(*, gym: Gym, member: Member) -> Membership | None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return current_membership_for_summary(gym=gym, member=member, today=gym_today(gym))


def member_self_payments(
    *,
    gym: Gym,
    member: Member,
    limit: int,
    after: str,
) -> tuple[list[Payment], dict[str, Any]]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return page_member_payments(
            gym_id=gym.id,
            member_id=member.id,
            limit=limit,
            after=after,
        )


def member_self_check_ins(
    *,
    gym: Gym,
    member: Member,
    limit: int,
    after: str,
) -> tuple[list[CheckIn], dict[str, Any]]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return page_self_check_ins(gym=gym, member_id=member.id, limit=limit, after=after)


def member_self_packages(
    *,
    gym: Gym,
    member: Member,
    status: str,
    package_type: str,
    limit: int,
    after: str,
) -> tuple[list[MemberServicePackage], dict[str, Any]]:
    scope = {
        "gym_id": str(gym.id),
        "member_id": str(member.id),
        "status": status,
        "package_type": package_type,
        "limit": str(limit),
        "ordering": "created_at DESC,id DESC",
    }
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        queryset = MemberServicePackage.objects.filter(gym_id=gym.id, member_id=member.id)
        if package_type:
            queryset = queryset.filter(package_type=package_type)
        queryset = _annotate_packages(queryset, gym=gym)
        if status:
            queryset = queryset.filter(effective_status=status)  # type: ignore[misc]
        queryset = queryset.order_by("-created_at", "-id")
        if after:
            created_at, row_id = _decode_package_cursor(after, scope=scope)
            queryset = queryset.filter(
                Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id)
            )
        rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=_PACKAGE_SALT,
            compress=True,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def member_self_schedule(
    *,
    gym: Gym,
    member: Member,
    date_from: date,
    date_to: date,
) -> dict[str, list[dict[str, object]]]:
    zone = ZoneInfo(gym.timezone)
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence_count = ClassOccurrence.objects.filter(
            gym_id=gym.id,
            status=ClassOccurrence.Status.SCHEDULED,
            local_date__gte=date_from,
            local_date__lte=date_to,
        ).count()
        if occurrence_count > _SCHEDULE_OCCURRENCE_LIMIT:
            raise ScheduleTooLarge()
        pt_rows = list(
            PTBooking.objects.select_related("trainer_staff_profile").filter(
                gym_id=gym.id,
                member_id=member.id,
                starts_at__lt=end,
                ends_at__gt=start,
            )
        )
        class_rows = list(
            ClassBooking.objects.select_related(
                "class_occurrence__class_type",
                "class_occurrence__trainer_staff_profile",
            ).filter(
                gym_id=gym.id,
                member_id=member.id,
                class_occurrence__starts_at__lt=end,
                class_occurrence__ends_at__gt=start,
            )
        )
        member_booking = (
            ClassBooking.objects.filter(
                gym_id=gym.id,
                class_occurrence_id=OuterRef("pk"),
                member_id=member.id,
            )
            .exclude(status=ClassBooking.Status.CANCELLED)
            .order_by("id")
            .values("id")[:1]
        )
        occurrences = list(
            ClassOccurrence.objects.select_related("class_type", "trainer_staff_profile")
            .filter(
                gym_id=gym.id,
                status=ClassOccurrence.Status.SCHEDULED,
                local_date__gte=date_from,
                local_date__lte=date_to,
            )
            .annotate(
                booked_count=Count(
                    "bookings",
                    filter=~Q(bookings__status=ClassBooking.Status.CANCELLED),
                ),
                member_booking_id=Subquery(member_booking),
            )
            .order_by("starts_at", "id")
        )
    bookings = [_pt_booking_item(row) for row in pt_rows]
    bookings.extend(_class_booking_item(row) for row in class_rows)
    bookings.sort(key=lambda item: (item["starts_at"], str(item["id"])))
    return {
        "bookings": bookings,
        "occurrences": [_occurrence_item(row) for row in occurrences],
    }


def member_class_booking_item(row: ClassBooking) -> dict[str, object]:
    return _class_booking_item(row)


def _feature_enabled(gym: Gym, code: str) -> bool:
    now = timezone.now()
    return GymFeatureEntitlement.objects.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(effective_until__isnull=True) | Q(effective_until__gt=now),
        gym=gym,
        feature__code=code,
        enabled=True,
    ).exists()


def _audit_value(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _decode_package_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(token, salt=_PACKAGE_SALT, max_age=_PACKAGE_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected = set(scope) | {"created_at", "id"}
        if set(loaded) != expected or any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        raw_time = loaded["created_at"]
        if not isinstance(raw_time, str):
            raise TypeError
        created_at = datetime.fromisoformat(raw_time)
        if created_at.tzinfo is None:
            raise ValueError
        row_id = UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return created_at, row_id


def _blank_location(value: str) -> str | None:
    return value or None


def _pt_booking_item(row: PTBooking) -> dict[str, object]:
    return {
        "id": row.id,
        "kind": "PT",
        "status": row.status,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "trainer_display_name": row.trainer_staff_profile.display_name,
        "class_name": None,
        "location": None,
        "member_service_package_id": row.member_service_package_id,
        "unmetered": row.unmetered,
    }


def _class_booking_item(row: ClassBooking) -> dict[str, object]:
    occurrence = row.class_occurrence
    return {
        "id": row.id,
        "kind": "CLASS",
        "status": row.status,
        "starts_at": occurrence.starts_at,
        "ends_at": occurrence.ends_at,
        "trainer_display_name": occurrence.trainer_staff_profile.display_name,
        "class_name": occurrence.class_type.name,
        "location": _blank_location(occurrence.location),
        "member_service_package_id": row.member_service_package_id,
        "unmetered": row.unmetered,
    }


def _occurrence_item(row: ClassOccurrence) -> dict[str, object]:
    booked = getattr(row, "booked_count", 0)
    member_booking_id = getattr(row, "member_booking_id", None)
    return {
        "id": row.id,
        "class_name": row.class_type.name,
        "trainer_display_name": row.trainer_staff_profile.display_name,
        "location": _blank_location(row.location),
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "capacity": row.capacity,
        "booked_count": int(booked),
        "member_booking_id": member_booking_id,
    }

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.members.exceptions import MemberConflict, MembershipPlanConflict
from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.members.normalization import (
    canonical_address,
    manual_sequence_value,
    normalize_member_email,
    normalize_member_gender,
    normalize_member_name,
    normalize_member_phone,
)
from apps.tenants.models import Gym, GymMembership, GymSettings

PLAN_LIST_CURSOR_SALT = "membership-plan-list"
PLAN_LIST_CURSOR_ORDERING = "code,id"
_PLAN_CURSOR_MAX_AGE = 24 * 60 * 60
_PLAN_MUTABLE_FIELDS = ("code", "name", "description", "duration_value", "duration_unit", "price")
MEMBER_LIST_CURSOR_SALT = "member-list"
MEMBER_LIST_CURSOR_ORDERING = "normalized_full_name,id"
_MEMBER_CURSOR_MAX_AGE = 24 * 60 * 60
_MEMBER_MUTABLE_FIELDS = (
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
)
_PROFILE_PHOTO_ERROR = "Select a clean profile photo from this gym."


def gym_today(gym: Gym) -> date:
    return timezone.localdate(timezone=ZoneInfo(gym.timezone))


def _add_duration(start: date, value: int, unit: str) -> date:
    if unit == MembershipPlan.DurationUnit.DAY:
        return start + timedelta(days=value - 1)
    if unit == MembershipPlan.DurationUnit.WEEK:
        return start + timedelta(weeks=value) - timedelta(days=1)
    months = value * (12 if unit == MembershipPlan.DurationUnit.YEAR else 1)
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


def _validate_member_dates(date_of_birth: date | None, join_date: date) -> None:
    if date_of_birth is not None and date_of_birth > join_date:
        raise ValidationError({"date_of_birth": ["Date of birth cannot be after the join date."]})


def _member_conflict(exc: IntegrityError) -> MemberConflict | None:
    message = str(exc)
    if "member_gym_number_uniq" in message:
        return MemberConflict("A member with this number already exists.")
    if "member_gym_linked_user_uniq" in message:
        return MemberConflict()
    return None


def _require_profile_photo(gym: Gym, asset_id: UUID | None) -> UUID | None:
    if asset_id is None:
        return None
    asset = FileAsset.objects.select_for_update().filter(gym=gym, id=asset_id).first()
    if (
        asset is None
        or asset.scan_status != FileAsset.ScanStatus.CLEAN
        or asset.retention_status != FileAsset.RetentionStatus.ACTIVE
        or asset.deleted_at is not None
    ):
        raise ValidationError({"profile_photo_asset_id": [_PROFILE_PHOTO_ERROR]})
    return asset.id


def _prepare_member_changes(changes: dict[str, Any]) -> dict[str, Any]:
    prepared = {field: changes[field] for field in _MEMBER_MUTABLE_FIELDS if field in changes}
    if "first_name" in prepared:
        prepared["first_name"] = normalize_member_name(str(prepared["first_name"]))
    if "last_name" in prepared:
        prepared["last_name"] = normalize_member_name(str(prepared["last_name"]))
    if "phone" in prepared:
        prepared["phone"] = normalize_member_phone(prepared["phone"])
    if "email" in prepared:
        prepared["email"] = normalize_member_email(prepared["email"])
    if "gender" in prepared:
        prepared["gender"] = normalize_member_gender(prepared["gender"])
    if "address" in prepared:
        prepared["address"] = canonical_address(prepared["address"])
    if "emergency_contact_name" in prepared:
        prepared["emergency_contact_name"] = normalize_member_name(
            str(prepared["emergency_contact_name"])
        )
    if "emergency_contact_phone" in prepared:
        prepared["emergency_contact_phone"] = (
            normalize_member_phone(prepared["emergency_contact_phone"]) or ""
        )
    if "notes" in prepared:
        prepared["notes"] = str(prepared["notes"]).strip()
    return prepared


def member_list_cursor_scope(
    *,
    gym_id: UUID,
    status: str,
    search: str,
    joined_from: date | None,
    joined_to: date | None,
    is_pt_client: bool | None,
) -> dict[str, str]:
    if is_pt_client is None:
        pt_client = ""
    else:
        pt_client = "true" if is_pt_client else "false"
    return {
        "gym_id": str(gym_id),
        "status": status,
        "search": search,
        "joined_from": joined_from.isoformat() if joined_from else "",
        "joined_to": joined_to.isoformat() if joined_to else "",
        "is_pt_client": pt_client,
        "ordering": MEMBER_LIST_CURSOR_ORDERING,
    }


def encode_member_cursor(
    *, normalized_full_name: str, member_id: UUID, scope: dict[str, str]
) -> str:
    return signing.dumps(
        {"normalized_full_name": normalized_full_name, "id": str(member_id), **scope},
        salt=MEMBER_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_member_cursor(token: str, *, scope: dict[str, str]) -> tuple[str, UUID]:
    try:
        cursor = signing.loads(token, salt=MEMBER_LIST_CURSOR_SALT, max_age=_MEMBER_CURSOR_MAX_AGE)
        if not isinstance(cursor, dict) or any(
            cursor.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        normalized_full_name = cursor["normalized_full_name"]
        if not isinstance(normalized_full_name, str):
            raise TypeError
        member_id = UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return normalized_full_name, member_id


def _member_search_filter(search: str) -> Q:
    folded = " ".join(search.split()).casefold()
    query = Q(normalized_full_name__icontains=folded) | Q(member_number__icontains=search.strip())
    lowered = search.strip().lower()
    if lowered:
        query |= Q(email__icontains=lowered)
    try:
        phone = normalize_member_phone(search)
    except ValueError:
        phone = None
    if phone:
        query |= Q(phone__icontains=phone)
    return query


def page_members(
    queryset: QuerySet[Member],
    *,
    gym_id: UUID,
    status: str,
    search: str,
    joined_from: date | None,
    joined_to: date | None,
    is_pt_client: bool | None,
    limit: int,
    after: str,
) -> tuple[list[Member], dict[str, Any]]:
    scope = member_list_cursor_scope(
        gym_id=gym_id,
        status=status,
        search=search,
        joined_from=joined_from,
        joined_to=joined_to,
        is_pt_client=is_pt_client,
    )
    if after:
        decode_member_cursor(after, scope=scope)
    if is_pt_client is True:
        return [], {"has_more": False, "next_after": None}
    queryset = queryset.order_by("normalized_full_name", "id")
    if status:
        queryset = queryset.filter(status=status)
    if joined_from is not None:
        queryset = queryset.filter(join_date__gte=joined_from)
    if joined_to is not None:
        queryset = queryset.filter(join_date__lte=joined_to)
    if search.strip():
        queryset = queryset.filter(_member_search_filter(search))
    if after:
        normalized_full_name, member_id = decode_member_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(normalized_full_name__gt=normalized_full_name)
            | Q(normalized_full_name=normalized_full_name, id__gt=member_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_member_cursor(
            normalized_full_name=last.normalized_full_name,
            member_id=last.id,
            scope=scope,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def _locked_member(*, gym: Gym, member_id: UUID) -> Member:
    try:
        return Member.objects.select_for_update().get(gym=gym, id=member_id)
    except Member.DoesNotExist as exc:
        raise Http404 from exc


@transaction.atomic
def create_member(
    *,
    gym: Gym,
    actor: GymMembership,
    member_number: str,
    first_name: str,
    last_name: str,
    phone: str | None,
    email: str | None,
    gender: str | None,
    date_of_birth: date | None,
    address: dict[str, Any],
    join_date: date,
    emergency_contact_name: str,
    emergency_contact_phone: str,
    notes: str,
    profile_photo_asset_id: UUID | None,
) -> Member:
    set_gym_context(gym_id=gym.id)
    _validate_member_dates(date_of_birth, join_date)
    settings = GymSettings.objects.select_for_update().get(gym=gym)
    requested_number = member_number.strip()
    advance_sequence = False
    if requested_number:
        if not settings.allow_manual_member_numbers:
            raise ValidationError({"member_number": ["Manual member numbers are disabled."]})
        assigned_number = requested_number
        consumed = manual_sequence_value(settings.member_number_prefix, assigned_number)
        if consumed is not None and consumed >= settings.next_member_number:
            settings.next_member_number = consumed + 1
            advance_sequence = True
    else:
        assigned_number = f"{settings.member_number_prefix}-{settings.next_member_number:06d}"
        settings.next_member_number += 1
        advance_sequence = True
    photo_id = _require_profile_photo(gym, profile_photo_asset_id)
    prepared = _prepare_member_changes(
        {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "gender": gender,
            "date_of_birth": date_of_birth,
            "address": address,
            "join_date": join_date,
            "emergency_contact_name": emergency_contact_name,
            "emergency_contact_phone": emergency_contact_phone,
            "notes": notes,
            "profile_photo_asset_id": photo_id,
        }
    )
    try:
        with transaction.atomic():
            if advance_sequence:
                settings.updated_by_membership_id = actor.id
                settings.save(
                    update_fields=[
                        "next_member_number",
                        "updated_by_membership_id",
                        "updated_at",
                    ]
                )
            return Member.objects.create(
                gym=gym,
                member_number=assigned_number,
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
                **prepared,
            )
    except IntegrityError as exc:
        conflict = _member_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise


@transaction.atomic
def update_member(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    changes: dict[str, Any],
) -> Member:
    set_gym_context(gym_id=gym.id)
    member = _locked_member(gym=gym, member_id=member_id)
    prepared = _prepare_member_changes(changes)
    if "profile_photo_asset_id" in prepared:
        prepared["profile_photo_asset_id"] = _require_profile_photo(
            gym, prepared["profile_photo_asset_id"]
        )
    _validate_member_dates(
        prepared["date_of_birth"] if "date_of_birth" in prepared else member.date_of_birth,
        prepared["join_date"] if "join_date" in prepared else member.join_date,
    )
    applied = {field: value for field, value in prepared.items() if getattr(member, field) != value}
    if not applied:
        return member
    for field, value in applied.items():
        setattr(member, field, value)
    member.updated_by_membership_id = actor.id
    try:
        with transaction.atomic():
            member.save(update_fields=[*applied.keys(), "updated_by_membership_id", "updated_at"])
    except IntegrityError as exc:
        conflict = _member_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return member


@transaction.atomic
def archive_member(*, gym: Gym, actor: GymMembership, member_id: UUID) -> Member:
    set_gym_context(gym_id=gym.id)
    member = _locked_member(gym=gym, member_id=member_id)
    if member.status == Member.Status.ARCHIVED and member.archived_at is not None:
        return member
    member.status = Member.Status.ARCHIVED
    if member.archived_at is None:
        member.archived_at = timezone.now()
    member.authz_version += 1
    member.updated_by_membership_id = actor.id
    member.save(
        update_fields=[
            "status",
            "archived_at",
            "authz_version",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    return member


def _plan_conflict(exc: IntegrityError) -> MembershipPlanConflict:
    if "mplan_gym_code_uniq" in str(exc):
        return MembershipPlanConflict("A membership plan with this code already exists.")
    return MembershipPlanConflict()


def encode_plan_cursor(*, code: str, plan_id: UUID, gym_id: UUID) -> str:
    return signing.dumps(
        {
            "code": code,
            "id": str(plan_id),
            "gym_id": str(gym_id),
            "ordering": PLAN_LIST_CURSOR_ORDERING,
        },
        salt=PLAN_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_plan_cursor(token: str, *, gym_id: UUID) -> tuple[str, UUID]:
    try:
        cursor = signing.loads(token, salt=PLAN_LIST_CURSOR_SALT, max_age=_PLAN_CURSOR_MAX_AGE)
        if (
            not isinstance(cursor, dict)
            or cursor.get("gym_id") != str(gym_id)
            or cursor.get("ordering") != PLAN_LIST_CURSOR_ORDERING
        ):
            raise signing.BadSignature
        code = cursor["code"]
        if not isinstance(code, str):
            raise TypeError
        plan_id = UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return code, plan_id


def page_membership_plans(
    queryset: QuerySet[MembershipPlan], *, gym_id: UUID, limit: int, after: str
) -> tuple[list[MembershipPlan], dict[str, Any]]:
    queryset = queryset.order_by("code", "id")
    if after:
        code, plan_id = decode_plan_cursor(after, gym_id=gym_id)
        queryset = queryset.filter(Q(code__gt=code) | Q(code=code, id__gt=plan_id))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_plan_cursor(code=last.code, plan_id=last.id, gym_id=gym_id)
    return rows, {"has_more": has_more, "next_after": next_after}


def _locked_plan(*, gym: Gym, plan_id: UUID) -> MembershipPlan:
    try:
        return MembershipPlan.objects.select_for_update().get(gym=gym, id=plan_id)
    except MembershipPlan.DoesNotExist as exc:
        raise Http404 from exc


@transaction.atomic
def create_plan(
    *,
    gym: Gym,
    actor: GymMembership,
    code: str,
    name: str,
    description: str,
    duration_value: int,
    duration_unit: str,
    price: Decimal,
) -> MembershipPlan:
    try:
        with transaction.atomic():
            return MembershipPlan.objects.create(
                gym=gym,
                code=code,
                name=name,
                description=description,
                duration_value=duration_value,
                duration_unit=duration_unit,
                price=price,
                currency=gym.currency,
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
            )
    except IntegrityError as exc:
        raise _plan_conflict(exc) from exc


@transaction.atomic
def update_plan(
    *,
    gym: Gym,
    actor: GymMembership,
    plan_id: UUID,
    changes: dict[str, Any],
) -> MembershipPlan:
    plan = _locked_plan(gym=gym, plan_id=plan_id)
    applied = {
        field: changes[field]
        for field in _PLAN_MUTABLE_FIELDS
        if field in changes and getattr(plan, field) != changes[field]
    }
    if not applied:
        return plan
    for field, value in applied.items():
        setattr(plan, field, value)
    plan.updated_by_membership_id = actor.id
    try:
        with transaction.atomic():
            plan.save(update_fields=[*applied.keys(), "updated_by_membership_id", "updated_at"])
    except IntegrityError as exc:
        raise _plan_conflict(exc) from exc
    return plan


@transaction.atomic
def archive_plan(*, gym: Gym, actor: GymMembership, plan_id: UUID) -> MembershipPlan:
    plan = _locked_plan(gym=gym, plan_id=plan_id)
    if not plan.is_active and plan.archived_at is not None:
        return plan
    plan.is_active = False
    if plan.archived_at is None:
        plan.archived_at = timezone.now()
    plan.updated_by_membership_id = actor.id
    plan.save(update_fields=["is_active", "archived_at", "updated_by_membership_id", "updated_at"])
    return plan


@transaction.atomic
def create_membership(
    *,
    gym: Gym,
    actor: GymMembership,
    member: Member,
    plan: MembershipPlan,
    start_date: date,
    discount_amount: Decimal,
) -> Membership:
    member = Member.objects.select_for_update().get(gym=gym, id=member.id)
    if member.status == Member.Status.ARCHIVED:
        raise ValidationError({"member_id": ["Archived members cannot receive memberships."]})
    expiry_date = _add_duration(start_date, plan.duration_value, plan.duration_unit)
    overlap = Membership.objects.filter(
        gym=gym,
        member=member,
        status__in=[
            Membership.Status.PENDING,
            Membership.Status.ACTIVE,
            Membership.Status.SUSPENDED,
        ],
        start_date__lte=expiry_date,
        expiry_date__gte=start_date,
    ).exists()
    if overlap:
        raise ValidationError({"start_date": ["Membership dates overlap an active membership."]})
    if discount_amount < 0 or discount_amount > plan.price:
        raise ValidationError(
            {"discount_amount": ["Discount must be between zero and plan price."]}
        )
    source = (
        Membership.RevenueSource.RENEWAL
        if Membership.objects.filter(gym=gym, member=member).exists()
        else Membership.RevenueSource.NEW_MEMBER
    )
    today = gym_today(gym)
    membership_status = Membership.Status.ACTIVE
    if start_date > today:
        membership_status = Membership.Status.PENDING
    elif expiry_date < today:
        membership_status = Membership.Status.EXPIRED
    try:
        return Membership.objects.create(
            gym=gym,
            member=member,
            plan=plan,
            plan_code=plan.code,
            plan_name=plan.name,
            duration_value=plan.duration_value,
            duration_unit=plan.duration_unit,
            start_date=start_date,
            expiry_date=expiry_date,
            list_price=plan.price,
            discount_amount=discount_amount,
            final_amount=plan.price - discount_amount,
            currency=plan.currency,
            status=membership_status,
            revenue_source=source,
            created_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"start_date": ["Membership dates overlap an existing membership."]}
        ) from exc


def _locked_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    return (
        Membership.objects.select_for_update()
        .select_related("member")
        .get(gym=gym, id=membership_id)
    )


@transaction.atomic
def suspend_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    if membership.status != Membership.Status.ACTIVE:
        raise ValidationError({"status": ["Only active memberships can be suspended."]})
    today = gym_today(gym)
    if membership.start_date > today or membership.expiry_date < today:
        raise ValidationError({"status": ["Membership is outside its active date range."]})
    membership.status = Membership.Status.SUSPENDED
    membership.save(update_fields=["status", "updated_at"])
    return membership


@transaction.atomic
def resume_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    if membership.status != Membership.Status.SUSPENDED:
        raise ValidationError({"status": ["Only suspended memberships can be resumed."]})
    today = gym_today(gym)
    if membership.start_date > today or membership.expiry_date < today:
        raise ValidationError({"status": ["Membership is outside its active date range."]})
    Member.objects.select_for_update().get(gym=gym, id=membership.member_id)
    overlap = (
        Membership.objects.filter(
            gym=gym,
            member_id=membership.member_id,
            status__in=[Membership.Status.PENDING, Membership.Status.ACTIVE],
            start_date__lte=membership.expiry_date,
            expiry_date__gte=membership.start_date,
        )
        .exclude(id=membership.id)
        .exists()
    )
    if overlap:
        raise ValidationError({"status": ["Membership overlaps another active membership."]})
    membership.status = Membership.Status.ACTIVE
    try:
        membership.save(update_fields=["status", "updated_at"])
    except IntegrityError as exc:
        raise ValidationError(
            {"status": ["Membership overlaps another active membership."]}
        ) from exc
    return membership


@transaction.atomic
def cancel_membership(
    *,
    gym: Gym,
    membership_id: UUID,
    reason: str,
) -> Membership:
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    allowed = {
        Membership.Status.PENDING,
        Membership.Status.ACTIVE,
        Membership.Status.SUSPENDED,
    }
    if membership.status not in allowed:
        raise ValidationError(
            {"status": ["Membership cannot be cancelled from its current state."]}
        )
    membership.status = Membership.Status.CANCELLED
    membership.cancelled_at = timezone.now()
    membership.cancellation_reason = reason
    membership.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancellation_reason",
            "updated_at",
        ]
    )
    return membership


@transaction.atomic
def refresh_due_memberships(*, gym: Gym, today: date, batch_size: int = 500) -> tuple[int, int]:
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch_size must be between 1 and 5000")
    due_ids = list(
        Membership.objects.select_for_update(skip_locked=True)
        .filter(gym=gym)
        .filter(
            Q(
                status__in=[
                    Membership.Status.ACTIVE,
                    Membership.Status.PENDING,
                    Membership.Status.SUSPENDED,
                ],
                expiry_date__lt=today,
            )
            | Q(
                status=Membership.Status.PENDING,
                start_date__lte=today,
                expiry_date__gte=today,
            )
        )
        .order_by("expiry_date", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not due_ids:
        return 0, 0
    expired = Membership.objects.filter(
        gym=gym,
        id__in=due_ids,
        status__in=[
            Membership.Status.ACTIVE,
            Membership.Status.PENDING,
            Membership.Status.SUSPENDED,
        ],
        expiry_date__lt=today,
    ).update(status=Membership.Status.EXPIRED)
    activated = Membership.objects.filter(
        gym=gym,
        id__in=due_ids,
        status=Membership.Status.PENDING,
        start_date__lte=today,
        expiry_date__gte=today,
    ).update(status=Membership.Status.ACTIVE)
    return activated, expired


def refresh_membership_payment_status(membership: Membership) -> None:
    entries = Payment.objects.filter(
        gym=membership.gym,
        membership=membership,
        status__in=[Payment.Status.POSTED, Payment.Status.VOID],
    )
    paid = sum(
        (
            -payment.amount
            if payment.entry_type in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
            else payment.amount
            for payment in entries
        ),
        Decimal("0"),
    )
    if paid <= 0:
        value = Membership.PaymentStatus.UNPAID
    elif paid < membership.final_amount:
        value = Membership.PaymentStatus.PARTIAL
    else:
        value = Membership.PaymentStatus.PAID
    if membership.payment_status != value:
        Membership.objects.filter(gym=membership.gym, id=membership.id).update(payment_status=value)


@transaction.atomic
def submit_payment(*, payment: Payment, actor: GymMembership) -> Payment:
    payment = Payment.objects.select_for_update().get(gym=payment.gym, id=payment.id)
    if payment.status != Payment.Status.DRAFT:
        raise ValidationError({"status": ["Only draft payments can be submitted."]})
    payment.status = Payment.Status.PENDING_APPROVAL
    payment.submitted_at = timezone.now()
    payment.submitted_by_membership_id = actor.id
    payment.updated_by_membership_id = actor.id
    payment.save()
    return payment


@transaction.atomic
def approve_payment(*, payment: Payment, actor: GymMembership) -> tuple[Payment, Receipt]:
    payment = (
        Payment.objects.select_for_update()
        .select_related("member", "membership")
        .get(gym=payment.gym, id=payment.id)
    )
    if payment.status != Payment.Status.PENDING_APPROVAL:
        raise ValidationError({"status": ["Only pending payments can be approved."]})
    if payment.submitted_by_membership_id == actor.id:
        raise ValidationError({"approver": ["Submitter cannot approve the same payment."]})
    if payment.membership is not None and payment.entry_type == Payment.EntryType.CHARGE:
        already_paid = Payment.objects.filter(
            gym=payment.gym,
            membership=payment.membership,
            status__in=[Payment.Status.POSTED, Payment.Status.VOID],
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if already_paid + payment.amount > payment.membership.final_amount:
            raise ValidationError(
                {"amount": ["Payment exceeds the authoritative membership balance."]}
            )
    payment.status = Payment.Status.POSTED
    payment.approved_at = timezone.now()
    payment.approved_by_membership_id = actor.id
    payment.updated_by_membership_id = actor.id
    payment.save()
    receipt = Receipt.objects.create(
        gym=payment.gym,
        payment=payment,
        receipt_number=f"{payment.business_date:%Y%m%d}-{str(payment.id).split('-')[0].upper()}",
        issued_at=timezone.now(),
        member_number=payment.member.member_number,
        member_name=payment.member.display_name,
        description=payment.description,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        business_date=payment.business_date,
        issuer_name=f"{actor.user.first_name} {actor.user.last_name}".strip() or actor.user.email,
        created_by_membership_id=actor.id,
    )
    membership = payment.membership
    if membership is not None:
        refresh_membership_payment_status(membership)
    return payment, receipt


@transaction.atomic
def refund_payment(
    *,
    payment: Payment,
    actor: GymMembership,
    amount: Decimal,
    reason: str,
) -> tuple[Payment, Receipt]:
    original = (
        Payment.objects.select_for_update()
        .select_related("member", "membership")
        .get(gym=payment.gym, id=payment.id)
    )
    if original.status != Payment.Status.POSTED or original.entry_type != Payment.EntryType.CHARGE:
        raise ValidationError({"status": ["Only a posted charge can be refunded."]})
    refunded = Payment.objects.filter(
        gym=original.gym,
        original_payment=original,
        entry_type=Payment.EntryType.REFUND,
        status=Payment.Status.POSTED,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if amount <= 0 or refunded + amount > original.amount:
        raise ValidationError({"amount": ["Refund exceeds the refundable balance."]})
    now = timezone.now()
    refund = Payment.objects.create(
        gym=original.gym,
        member=original.member,
        membership=original.membership,
        original_payment=original,
        entry_type=Payment.EntryType.REFUND,
        purpose=original.purpose,
        description=f"Refund: {reason}",
        amount=amount,
        currency=original.currency,
        method=original.method,
        business_date=timezone.localdate(),
        received_at=now,
        status=Payment.Status.POSTED,
        approved_at=now,
        approved_by_membership_id=actor.id,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    receipt = Receipt.objects.create(
        gym=refund.gym,
        payment=refund,
        receipt_number=f"{refund.business_date:%Y%m%d}-{str(refund.id).split('-')[0].upper()}",
        issued_at=now,
        member_number=refund.member.member_number,
        member_name=refund.member.display_name,
        description=refund.description,
        amount=refund.amount,
        currency=refund.currency,
        method=refund.method,
        business_date=refund.business_date,
        issuer_name=f"{actor.user.first_name} {actor.user.last_name}".strip() or actor.user.email,
        created_by_membership_id=actor.id,
    )
    if refund.membership is not None:
        refresh_membership_payment_status(refund.membership)
    return refund, receipt


@transaction.atomic
def void_payment(*, payment: Payment, actor: GymMembership, reason: str) -> tuple[Payment, Payment]:
    reversal, _receipt = refund_payment(
        payment=payment,
        actor=actor,
        amount=payment.amount,
        reason=f"Void: {reason}",
    )
    reversal.entry_type = Payment.EntryType.REVERSAL
    reversal.save(update_fields=["entry_type", "updated_at"])
    original = Payment.objects.select_for_update().get(gym=payment.gym, id=payment.id)
    original.status = Payment.Status.VOID
    original.voided_at = timezone.now()
    original.void_reason = reason
    original.updated_by_membership_id = actor.id
    original.save(
        update_fields=[
            "status",
            "voided_at",
            "void_reason",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    return original, reversal

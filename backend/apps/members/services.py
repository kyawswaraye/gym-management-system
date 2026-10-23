from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.members.checkin_services import member_check_in_summary
from apps.members.exceptions import (
    MemberConflict,
    MembershipOverlap,
    MembershipPlanConflict,
    MembershipStateConflict,
)
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
MEMBERSHIP_LIST_CURSOR_SALT = "membership-list"
MEMBERSHIP_LIST_ORDERING = "start_date DESC,id DESC"
MEMBERSHIP_EXPIRY_CURSOR_SALT = "membership-expiry"
MEMBERSHIP_EXPIRY_ORDERING = "expiry_date ASC,id ASC"
_MEMBERSHIP_CURSOR_MAX_AGE = 24 * 60 * 60
_MEMBERSHIP_NOTE_LIMIT = 4000
_MEMBERSHIP_REASON_LIMIT = 500
_MONEY = Decimal("0.01")
EXPIRY_BUCKETS = (
    "expired",
    "today",
    "within_3_days",
    "within_7_days",
    "within_30_days",
)
_OVERLAP_STATUSES = (
    Membership.Status.PENDING,
    Membership.Status.ACTIVE,
    Membership.Status.SUSPENDED,
)
_PLAN_MUTABLE_FIELDS = ("code", "name", "description", "duration_value", "duration_unit", "price")
MEMBER_LIST_CURSOR_SALT = "member-list"
MEMBER_LIST_CURSOR_ORDERING = "normalized_full_name,id"
_MEMBER_CURSOR_MAX_AGE = 24 * 60 * 60
MEMBER_PAYMENT_HISTORY_CURSOR_SALT = "member-payment-history"
MEMBER_PAYMENT_HISTORY_ORDERING = "received_at DESC,id DESC"
_MEMBER_PAYMENT_HISTORY_CURSOR_MAX_AGE = 24 * 60 * 60
_MEMBER_PAYMENT_HISTORY_POSITION_KEYS = frozenset({"received_at", "id"})
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
_PROFILE_PHOTO_CREATE_ERROR = "Create the member before attaching a profile photo."


def gym_today(gym: Gym) -> date:
    return timezone.localdate(timezone=ZoneInfo(gym.timezone))


MEMBERSHIP_SUMMARY_ORDER = ("-start_date", "-created_at", "-id")
MEMBERSHIP_SUMMARY_HISTORY_LIMIT = 20
_MEMBERSHIP_SUMMARY_FETCH_LIMIT = MEMBERSHIP_SUMMARY_HISTORY_LIMIT + 1
_FINALIZED_PAYMENT_STATUSES = (Payment.Status.POSTED, Payment.Status.VOID)
_MONEY_OUTPUT: DecimalField[Any, Any] = DecimalField(max_digits=18, decimal_places=2)


def _zero_money() -> Value:
    return Value(Decimal("0.00"), output_field=_MONEY_OUTPUT)


class MemberPaymentSummary(TypedDict):
    available: bool
    currency: str
    total_charged: Decimal
    total_refunded: Decimal
    net_paid: Decimal
    outstanding_membership_balance: Decimal


def inclusive_days_remaining(expiry: date, today: date) -> int:
    """Inclusive gym-local days from today through expiry, never below zero."""

    return max(0, (expiry - today).days + 1)


def _money(value: object) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_MONEY)
    return Decimal(str(value)).quantize(_MONEY)


def current_membership_for_summary(*, gym: Gym, member: Member, today: date) -> Membership | None:
    """Choose the membership valid on the gym-local today.

    Pending, active, and suspended ranges cannot overlap, so this is normally
    unique. The order still makes a concurrent or repaired tie deterministic.
    """

    return (
        Membership.objects.filter(
            gym=gym,
            member=member,
            status__in=(
                Membership.Status.PENDING,
                Membership.Status.ACTIVE,
                Membership.Status.SUSPENDED,
            ),
            start_date__lte=today,
            expiry_date__gte=today,
        )
        .order_by(*MEMBERSHIP_SUMMARY_ORDER)
        .first()
    )


def membership_history_for_summary(*, gym: Gym, member: Member) -> tuple[list[Membership], bool]:
    """Return the latest 20 memberships and whether an older row exists.

    One query fetches 21 rows. The extra row sets ``has_more`` and is not loaded
    into the response, so history length cannot add queries or payload rows.
    """

    rows = list(
        Membership.objects.filter(gym=gym, member=member).order_by(*MEMBERSHIP_SUMMARY_ORDER)[
            :_MEMBERSHIP_SUMMARY_FETCH_LIMIT
        ]
    )
    return rows[:MEMBERSHIP_SUMMARY_HISTORY_LIMIT], len(rows) > MEMBERSHIP_SUMMARY_HISTORY_LIMIT


def _linked_payment_total() -> Subquery:
    signed = Sum(
        Case(
            When(entry_type=Payment.EntryType.CHARGE, then=F("amount")),
            When(
                entry_type__in=(Payment.EntryType.REFUND, Payment.EntryType.REVERSAL),
                then=-F("amount"),
            ),
            default=_zero_money(),
            output_field=_MONEY_OUTPUT,
        )
    )
    totals = (
        Payment.objects.filter(
            gym_id=OuterRef("gym_id"),
            membership_id=OuterRef("pk"),
            status__in=_FINALIZED_PAYMENT_STATUSES,
        )
        .order_by()
        .values("membership_id")
        .annotate(signed=signed)
        .values("signed")[:1]
    )
    return Subquery(totals, output_field=_MONEY_OUTPUT)


def member_payment_summary(*, gym: Gym, member: Member) -> MemberPaymentSummary:
    """Aggregate finalized ledger totals without loading payment rows.

    Two queries stay constant as history grows. The first conditionally sums the
    member's POSTED and VOID charges, refunds, and reversals. The second sums
    ``max(0, final_amount - signed linked payments)`` across non-cancelled
    memberships. Unlinked payments are absent from that correlated sum, so they
    cannot reduce outstanding. Draft, pending, rejected, and cancelled payments
    are excluded. ``available`` is true because Payment and Membership are the
    authoritative migrated tables; this read does not publish payment rows.
    """

    totals = Payment.objects.filter(
        gym=gym,
        member=member,
        status__in=_FINALIZED_PAYMENT_STATUSES,
    ).aggregate(
        charged=Coalesce(
            Sum("amount", filter=Q(entry_type=Payment.EntryType.CHARGE)),
            _zero_money(),
        ),
        refunded=Coalesce(
            Sum(
                "amount",
                filter=Q(entry_type__in=(Payment.EntryType.REFUND, Payment.EntryType.REVERSAL)),
            ),
            _zero_money(),
        ),
    )
    charged = _money(totals["charged"])
    refunded = _money(totals["refunded"])
    signed_paid = Coalesce(_linked_payment_total(), _zero_money())
    balance = Greatest(
        ExpressionWrapper(F("final_amount") - signed_paid, output_field=_MONEY_OUTPUT),
        _zero_money(),
    )
    outstanding = (
        Membership.objects.filter(gym=gym, member=member)
        .exclude(status=Membership.Status.CANCELLED)
        .annotate(balance=balance)
        .aggregate(total=Coalesce(Sum("balance"), _zero_money()))
    )
    return {
        "available": True,
        "currency": gym.currency,
        "total_charged": charged,
        "total_refunded": refunded,
        "net_paid": _money(max(Decimal("0.00"), charged - refunded)),
        "outstanding_membership_balance": _money(outstanding["total"]),
    }


def member_summary(*, gym: Gym, member: Member) -> dict[str, object]:
    """Assemble the published summary with a fixed query budget.

    After the caller loads the same-gym member, this performs six queries:
    the current-membership lookup (LIMIT 1), the 21-row history fetch, the
    finalized payment aggregate, the outstanding-balance aggregate, the latest
    check-in, and the open check-in. Both check-in reads stay at one row.
    ``is_pt_client`` is not stored here. The caller annotates the member from
    the active assignment that covers the gym-local as-of date, and both
    summary fields read that badge.
    """

    today = gym_today(gym)
    current = current_membership_for_summary(gym=gym, member=member, today=today)
    history, has_more = membership_history_for_summary(gym=gym, member=member)
    days_remaining = (
        inclusive_days_remaining(current.expiry_date, today) if current is not None else None
    )
    return {
        "member": member,
        "current_membership": current,
        "days_remaining": days_remaining,
        "membership_history": history,
        "membership_history_has_more": has_more,
        "payment_summary": member_payment_summary(gym=gym, member=member),
        "check_in_summary": member_check_in_summary(gym=gym, member=member),
    }


def member_payment_history_scope(*, gym_id: UUID, member_id: UUID, limit: int) -> dict[str, str]:
    return {
        "gym_id": str(gym_id),
        "member_id": str(member_id),
        "limit": str(limit),
        "ordering": MEMBER_PAYMENT_HISTORY_ORDERING,
    }


def encode_member_payment_cursor(
    *, received_at: datetime, payment_id: UUID, scope: dict[str, str]
) -> str:
    """Return an HMAC-signed cursor. Django's timestamp signer supplies the MAC."""

    return signing.dumps(
        {"received_at": received_at.isoformat(), "id": str(payment_id), **scope},
        salt=MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
        compress=True,
    )


def decode_member_payment_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(
            token,
            salt=MEMBER_PAYMENT_HISTORY_CURSOR_SALT,
            max_age=_MEMBER_PAYMENT_HISTORY_CURSOR_MAX_AGE,
        )
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected_keys = set(scope) | _MEMBER_PAYMENT_HISTORY_POSITION_KEYS
        if set(loaded) != expected_keys or any(
            loaded.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        received_raw = loaded["received_at"]
        if not isinstance(received_raw, str):
            raise TypeError
        received_at = datetime.fromisoformat(received_raw)
        if received_at.tzinfo is None:
            raise ValueError
        payment_id = UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return received_at, payment_id


def page_member_payments(
    *,
    gym_id: UUID,
    member_id: UUID,
    limit: int,
    after: str,
) -> tuple[list[Payment], dict[str, Any]]:
    """Return one history page ordered by received_at DESC, id DESC.

    The cursor binds the gym, member, that order, and the page limit. One query
    fetches ``limit + 1`` rows and joins the same-gym receipt.
    ``payment_gym_member_hist_idx`` matches that order so the limit can stop
    without sorting the member's full history.
    """

    scope = member_payment_history_scope(gym_id=gym_id, member_id=member_id, limit=limit)
    received_at: datetime | None = None
    payment_id: UUID | None = None
    if after:
        received_at, payment_id = decode_member_payment_cursor(after, scope=scope)
    issued_number = Receipt.objects.filter(
        gym_id=OuterRef("gym_id"),
        payment_id=OuterRef("pk"),
        status=Receipt.Status.ISSUED,
    ).values("receipt_number")[:1]
    queryset = Payment.objects.filter(gym_id=gym_id, member_id=member_id).annotate(
        history_receipt_number=Subquery(issued_number)
    )
    queryset = queryset.order_by("-received_at", "-id")
    if received_at is not None and payment_id is not None:
        queryset = queryset.filter(
            Q(received_at__lt=received_at) | Q(received_at=received_at, id__lt=payment_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_member_payment_cursor(
            received_at=last.received_at,
            payment_id=last.id,
            scope=scope,
        )
    return cast(list[Payment], rows), {"has_more": has_more, "next_after": next_after}


def inclusive_expiry_date(start: date, value: int, unit: str) -> date:
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


def _add_duration(start: date, value: int, unit: str) -> date:
    return inclusive_expiry_date(start, value, unit)


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


def _require_profile_photo(gym: Gym, asset_id: UUID | None, *, member_id: UUID) -> UUID | None:
    if asset_id is None:
        return None
    asset = FileAsset.objects.select_for_update().filter(gym=gym, id=asset_id).first()
    if (
        asset is None
        or asset.purpose != "PROFILE_PHOTO"
        or asset.owner_type != "MEMBER"
        or asset.owner_id != member_id
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
    as_of: date,
    limit: int,
    after: str,
) -> tuple[list[Member], dict[str, Any]]:
    from apps.trainers.selectors import annotate_member_pt_client

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
    queryset = annotate_member_pt_client(queryset, gym_id=gym_id, as_of=as_of)
    if is_pt_client is not None:
        queryset = queryset.filter(pt_client_badge=is_pt_client)  # type: ignore[misc]
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


def _deny_trainer_member_command(*, gym_id: UUID, actor: GymMembership) -> None:
    """Fail closed when no trainer create, edit, archive, or restore contract exists."""

    from apps.trainers.selectors import trainer_member_scope

    if trainer_member_scope(gym_id=gym_id, actor=actor).restricted:
        raise Http404


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
    _deny_trainer_member_command(gym_id=gym.id, actor=actor)
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
    if profile_photo_asset_id is not None:
        raise ValidationError({"profile_photo_asset_id": [_PROFILE_PHOTO_CREATE_ERROR]})
    photo_id = None
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
    _deny_trainer_member_command(gym_id=gym.id, actor=actor)
    member = _locked_member(gym=gym, member_id=member_id)
    prepared = _prepare_member_changes(changes)
    if "profile_photo_asset_id" in prepared:
        prepared["profile_photo_asset_id"] = _require_profile_photo(
            gym, prepared["profile_photo_asset_id"], member_id=member.id
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
    _deny_trainer_member_command(gym_id=gym.id, actor=actor)
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
    from apps.users.member_sessions import revoke_member_sessions

    revoke_member_sessions(gym_id=gym.id, member_id=member.id, reason="MEMBER_ARCHIVED")
    return member


@transaction.atomic
def restore_member(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Member:
    """Return an archived member to ACTIVE.

    A member that is already not archived is unchanged: no ``authz_version``
    move, no updater write, and no audit row. A real transition clears
    ``archived_at``, records the updater, increments ``authz_version`` once,
    and writes one tenant audit event in the same locked transaction.
    """

    set_gym_context(gym_id=gym.id)
    _deny_trainer_member_command(gym_id=gym.id, actor=actor)
    member = _locked_member(gym=gym, member_id=member_id)
    if member.status != Member.Status.ARCHIVED and member.archived_at is None:
        return member
    previous_status = member.status
    previous_archived_at = member.archived_at
    previous_version = member.authz_version
    member.status = Member.Status.ACTIVE
    member.archived_at = None
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
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="MEMBER_RESTORED",
        module="members",
        target_type="Member",
        target_id=member.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values={
            "status": previous_status,
            "archived_at": (
                None if previous_archived_at is None else previous_archived_at.isoformat()
            ),
            "authz_version": previous_version,
        },
        new_values={
            "status": member.status,
            "archived_at": None,
            "authz_version": member.authz_version,
        },
    )
    return member


@transaction.atomic
def link_member_identity(*, gym: Gym, member_id: UUID, user: Any) -> Member:
    """Link an existing user to one active member. This is not an HTTP route.

    Owner and member onboarding stays unpublished. Tests and future approved
    workflows call this so a link increments ``authz_version`` and revokes
    that member's sessions. A second member for the same user in another gym
    is allowed by the database and is not a login selector.
    """

    from apps.users.member_sessions import revoke_member_sessions

    set_gym_context(gym_id=gym.id)
    member = _locked_member(gym=gym, member_id=member_id)
    if member.status != Member.Status.ACTIVE:
        raise ValidationError({"status": ["Only an active member can be linked."]})
    if not getattr(user, "is_active", False):
        raise ValidationError({"linked_user_id": ["The user is inactive."]})
    if member.linked_user_id == user.id:
        return member
    member.linked_user_id = user.id
    member.authz_version += 1
    try:
        member.save(update_fields=["linked_user", "authz_version", "updated_at"])
    except IntegrityError as exc:
        raise ValidationError(
            {"linked_user_id": ["This user is already linked to a member in this gym."]}
        ) from exc
    revoke_member_sessions(gym_id=gym.id, member_id=member.id, reason="MEMBER_LINK_CHANGED")
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


def _integrity_text(exc: IntegrityError) -> str:
    parts = [str(exc)]
    cause = exc.__cause__
    if cause is not None:
        parts.append(str(cause))
    return " ".join(parts)


def _membership_integrity(exc: IntegrityError) -> MembershipOverlap | MembershipStateConflict:
    if "membership_no_active_overlap_excl" in _integrity_text(exc):
        return MembershipOverlap()
    return MembershipStateConflict("The membership could not be saved.")


def _membership_list_scope(
    *,
    gym_id: UUID,
    status: str,
    member_id: UUID | None,
    payment_status: str,
) -> dict[str, str]:
    return {
        "gym_id": str(gym_id),
        "status": status,
        "member_id": "" if member_id is None else str(member_id),
        "payment_status": payment_status,
        "ordering": MEMBERSHIP_LIST_ORDERING,
    }


def encode_membership_cursor(
    *, start_date: date, membership_id: UUID, scope: dict[str, str]
) -> str:
    return signing.dumps(
        {"start_date": start_date.isoformat(), "id": str(membership_id), **scope},
        salt=MEMBERSHIP_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_membership_cursor(token: str, *, scope: dict[str, str]) -> tuple[date, UUID]:
    try:
        cursor = signing.loads(
            token, salt=MEMBERSHIP_LIST_CURSOR_SALT, max_age=_MEMBERSHIP_CURSOR_MAX_AGE
        )
        if not isinstance(cursor, dict) or any(
            cursor.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        start_date = date.fromisoformat(str(cursor["start_date"]))
        membership_id = UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return start_date, membership_id


def page_memberships(
    queryset: QuerySet[Membership],
    *,
    gym_id: UUID,
    status: str,
    member_id: UUID | None,
    payment_status: str,
    limit: int,
    after: str,
) -> tuple[list[Membership], dict[str, Any]]:
    scope = _membership_list_scope(
        gym_id=gym_id,
        status=status,
        member_id=member_id,
        payment_status=payment_status,
    )
    queryset = queryset.order_by("-start_date", "-id")
    if status:
        queryset = queryset.filter(status=status)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if payment_status:
        queryset = queryset.filter(payment_status=payment_status)
    if after:
        cursor_start, cursor_id = decode_membership_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(start_date__lt=cursor_start) | Q(start_date=cursor_start, id__lt=cursor_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_membership_cursor(
            start_date=last.start_date, membership_id=last.id, scope=scope
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def membership_expiry_filter(*, bucket: str, today: date) -> Q:
    open_statuses = [Membership.Status.ACTIVE, Membership.Status.SUSPENDED]
    if bucket == "expired":
        return Q(
            status__in=[
                Membership.Status.ACTIVE,
                Membership.Status.SUSPENDED,
                Membership.Status.EXPIRED,
            ],
            expiry_date__lt=today,
        )
    if bucket == "today":
        return Q(status__in=open_statuses, expiry_date=today)
    if bucket == "within_3_days":
        return Q(
            status__in=open_statuses,
            expiry_date__gt=today,
            expiry_date__lte=today + timedelta(days=3),
        )
    if bucket == "within_7_days":
        return Q(
            status__in=open_statuses,
            expiry_date__gt=today + timedelta(days=3),
            expiry_date__lte=today + timedelta(days=7),
        )
    if bucket == "within_30_days":
        return Q(
            status__in=open_statuses,
            expiry_date__gt=today + timedelta(days=7),
            expiry_date__lte=today + timedelta(days=30),
        )
    raise ValidationError({"bucket": ["Unknown membership expiry bucket."]})


def _expiry_scope(*, gym_id: UUID, bucket: str) -> dict[str, str]:
    return {
        "gym_id": str(gym_id),
        "bucket": bucket,
        "ordering": MEMBERSHIP_EXPIRY_ORDERING,
    }


def encode_expiry_cursor(*, expiry_date: date, membership_id: UUID, scope: dict[str, str]) -> str:
    return signing.dumps(
        {"expiry_date": expiry_date.isoformat(), "id": str(membership_id), **scope},
        salt=MEMBERSHIP_EXPIRY_CURSOR_SALT,
        compress=True,
    )


def decode_expiry_cursor(token: str, *, scope: dict[str, str]) -> tuple[date, UUID]:
    try:
        cursor = signing.loads(
            token, salt=MEMBERSHIP_EXPIRY_CURSOR_SALT, max_age=_MEMBERSHIP_CURSOR_MAX_AGE
        )
        if not isinstance(cursor, dict) or any(
            cursor.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        expiry_date = date.fromisoformat(str(cursor["expiry_date"]))
        membership_id = UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return expiry_date, membership_id


def page_membership_expiry(
    queryset: QuerySet[Membership],
    *,
    gym_id: UUID,
    bucket: str,
    today: date,
    limit: int,
    after: str,
) -> tuple[list[Membership], dict[str, Any]]:
    scope = _expiry_scope(gym_id=gym_id, bucket=bucket)
    queryset = queryset.filter(membership_expiry_filter(bucket=bucket, today=today)).order_by(
        "expiry_date", "id"
    )
    if after:
        cursor_expiry, cursor_id = decode_expiry_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(expiry_date__gt=cursor_expiry) | Q(expiry_date=cursor_expiry, id__gt=cursor_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_expiry_cursor(
            expiry_date=last.expiry_date, membership_id=last.id, scope=scope
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def _dates_cover_today(membership: Membership, today: date) -> bool:
    return membership.start_date <= today <= membership.expiry_date


def _locked_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    try:
        member_id = Membership.objects.values_list("member_id", flat=True).get(
            gym=gym, id=membership_id
        )
    except Membership.DoesNotExist as exc:
        raise Http404 from exc
    Member.objects.select_for_update().get(gym=gym, id=member_id)
    return Membership.objects.select_for_update().get(gym=gym, id=membership_id)


@transaction.atomic
def create_membership(
    *,
    gym: Gym,
    actor: GymMembership,
    member: Member,
    plan: MembershipPlan,
    start_date: date,
    discount_amount: Decimal,
    note: str = "",
) -> Membership:
    set_gym_context(gym_id=gym.id)
    from apps.trainers.selectors import require_assigned_member

    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member.id,
        as_of=gym_today(gym),
    )
    try:
        member = Member.objects.select_for_update().get(gym=gym, id=member.id)
    except Member.DoesNotExist as exc:
        raise Http404 from exc
    try:
        plan = MembershipPlan.objects.select_for_update().get(gym=gym, id=plan.id)
    except MembershipPlan.DoesNotExist as exc:
        raise Http404 from exc
    if member.status != Member.Status.ACTIVE:
        raise MembershipStateConflict("Only an active member can receive a membership.")
    if not plan.is_active:
        raise MembershipStateConflict("Only an active plan can be sold.")
    if len(note) > _MEMBERSHIP_NOTE_LIMIT:
        raise ValidationError({"note": ["Note is too long."]})
    discount_amount = discount_amount.quantize(_MONEY)
    list_price = plan.price.quantize(_MONEY)
    if discount_amount < 0 or discount_amount > list_price:
        raise ValidationError(
            {"discount_amount": ["Discount must be between zero and the plan list price."]}
        )
    expiry_date = inclusive_expiry_date(start_date, plan.duration_value, plan.duration_unit)
    overlap = Membership.objects.filter(
        gym=gym,
        member=member,
        status__in=_OVERLAP_STATUSES,
        start_date__lte=expiry_date,
        expiry_date__gte=start_date,
    ).exists()
    if overlap:
        raise MembershipOverlap()
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
        with transaction.atomic():
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
                list_price=list_price,
                discount_amount=discount_amount,
                final_amount=(list_price - discount_amount).quantize(_MONEY),
                currency=plan.currency,
                status=membership_status,
                revenue_source=source,
                note=note,
                created_by_membership_id=actor.id,
            )
    except IntegrityError as exc:
        raise _membership_integrity(exc) from exc


@transaction.atomic
def update_membership_note(*, gym: Gym, membership_id: UUID, note: str) -> Membership:
    if len(note) > _MEMBERSHIP_NOTE_LIMIT:
        raise ValidationError({"note": ["Note is too long."]})
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    if membership.note == note:
        return membership
    membership.note = note
    membership.save(update_fields=["note", "updated_at"])
    return membership


@transaction.atomic
def suspend_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    today = gym_today(gym)
    eligible = _dates_cover_today(membership, today)
    if membership.status == Membership.Status.SUSPENDED:
        if not eligible:
            raise MembershipStateConflict("Membership is outside its active date range.")
        return membership
    if membership.status != Membership.Status.ACTIVE:
        raise MembershipStateConflict("Only an active membership can be suspended.")
    if not eligible:
        raise MembershipStateConflict("Membership is outside its active date range.")
    membership.status = Membership.Status.SUSPENDED
    membership.save(update_fields=["status", "updated_at"])
    return membership


@transaction.atomic
def resume_membership(*, gym: Gym, membership_id: UUID) -> Membership:
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    today = gym_today(gym)
    eligible = _dates_cover_today(membership, today)
    if membership.status == Membership.Status.ACTIVE:
        if not eligible:
            raise MembershipStateConflict("Membership is outside its active date range.")
        return membership
    if membership.status != Membership.Status.SUSPENDED:
        raise MembershipStateConflict("Only a suspended membership can be resumed.")
    if not eligible:
        raise MembershipStateConflict("Membership is outside its active date range.")
    overlap = (
        Membership.objects.filter(
            gym=gym,
            member_id=membership.member_id,
            status__in=_OVERLAP_STATUSES,
            start_date__lte=membership.expiry_date,
            expiry_date__gte=membership.start_date,
        )
        .exclude(id=membership.id)
        .exists()
    )
    if overlap:
        raise MembershipOverlap()
    membership.status = Membership.Status.ACTIVE
    try:
        with transaction.atomic():
            membership.save(update_fields=["status", "updated_at"])
    except IntegrityError as exc:
        raise _membership_integrity(exc) from exc
    return membership


@transaction.atomic
def cancel_membership(
    *,
    gym: Gym,
    actor: GymMembership,
    membership_id: UUID,
    reason: str,
) -> Membership:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > _MEMBERSHIP_REASON_LIMIT:
        raise ValidationError({"reason": ["Enter a cancellation reason."]})
    membership = _locked_membership(gym=gym, membership_id=membership_id)
    if membership.status == Membership.Status.CANCELLED:
        if (
            membership.cancellation_reason == cleaned
            and membership.cancelled_by_membership_id == actor.id
        ):
            return membership
        raise MembershipStateConflict("Membership cannot be cancelled from its current state.")
    if membership.status not in {
        Membership.Status.PENDING,
        Membership.Status.ACTIVE,
        Membership.Status.SUSPENDED,
    }:
        raise MembershipStateConflict("Membership cannot be cancelled from its current state.")
    membership.status = Membership.Status.CANCELLED
    membership.cancelled_at = timezone.now()
    membership.cancelled_by_membership_id = actor.id
    membership.cancellation_reason = cleaned
    try:
        with transaction.atomic():
            membership.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "cancelled_by_membership_id",
                    "cancellation_reason",
                    "updated_at",
                ]
            )
    except IntegrityError as exc:
        raise _membership_integrity(exc) from exc
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
    entries = list(
        Payment.objects.filter(gym=membership.gym, membership=membership).filter(
            Q(
                entry_type=Payment.EntryType.CHARGE,
                status__in=[Payment.Status.POSTED, Payment.Status.VOID],
            )
            | Q(
                entry_type__in=[Payment.EntryType.REFUND, Payment.EntryType.REVERSAL],
                status=Payment.Status.POSTED,
            )
        )
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
    has_refund = any(
        payment.entry_type in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
        for payment in entries
    )
    if paid <= 0 and not has_refund:
        value = Membership.PaymentStatus.UNPAID
    elif paid <= 0:
        value = Membership.PaymentStatus.REFUNDED
    elif has_refund and paid < membership.final_amount:
        value = Membership.PaymentStatus.PARTIALLY_REFUNDED
    elif paid < membership.final_amount:
        value = Membership.PaymentStatus.PARTIAL
    else:
        value = Membership.PaymentStatus.PAID
    if membership.payment_status != value:
        Membership.objects.filter(gym=membership.gym, id=membership.id).update(payment_status=value)

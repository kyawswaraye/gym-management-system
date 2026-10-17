# Deterministic, restart-safe reconciliation of legacy payment and receipt rows.
# This migration fails with counts and writes nothing when evidence is missing.

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db import migrations

_NAMESPACE = uuid.UUID("6c8b1c2e-4a1d-5b7e-9f30-1a2b3c4d5e6f")


def reconcile_legacy_payments(apps: Any, schema_editor: Any) -> None:
    Payment = apps.get_model("members", "Payment")
    Receipt = apps.get_model("members", "Receipt")
    Member = apps.get_model("members", "Member")
    Membership = apps.get_model("members", "Membership")
    Gym = apps.get_model("tenants", "Gym")
    GymMembership = apps.get_model("tenants", "GymMembership")
    User = apps.get_model("users", "User")
    StaffProfile = apps.get_model("tenants", "StaffProfile")

    payments = list(Payment.objects.order_by("gym_id", "received_at", "id"))
    receipts = list(Receipt.objects.order_by("gym_id", "issued_at", "id"))
    payment_by_key = {(row.gym_id, row.id): row for row in payments}
    receipts_by_payment: dict[tuple[Any, Any], list[Any]] = defaultdict(list)
    for receipt in receipts:
        receipts_by_payment[(receipt.gym_id, receipt.payment_id)].append(receipt)

    members = {row.id: row for row in Member.objects.all()}
    memberships = {row.id: row for row in Membership.objects.all()}
    gyms = {row.id: row for row in Gym.objects.all()}
    staff_ids = {(row.gym_id, row.id) for row in GymMembership.objects.all()}
    users = {row.id: row for row in User.objects.all()}
    staff_user = {
        (row.gym_id, row.id): row.user_id
        for row in GymMembership.objects.all().only("id", "gym_id", "user_id")
    }
    profiles = {
        (row.gym_id, row.gym_membership_id): row.display_name
        for row in StaffProfile.objects.all().only("gym_id", "gym_membership_id", "display_name")
    }

    problems: dict[str, int] = {
        "missing_lifecycle_evidence": 0,
        "contradictory_lifecycle": 0,
        "charge_sod_violation": 0,
        "cross_gym": 0,
        "membership_member_mismatch": 0,
        "posted_exceeds_obligation": 0,
        "correction_without_original": 0,
        "unsupported_source": 0,
        "issuer_name_unusable": 0,
        "legacy_number_collision": 0,
    }

    def bump(name: str) -> None:
        problems[name] += 1

    paid: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for payment in payments:
        if payment.purpose in {"PT", "PRODUCT", "CLASS"}:
            bump("unsupported_source")
        member = members.get(payment.member_id) if payment.member_id else None
        if payment.member_id and (member is None or member.gym_id != payment.gym_id):
            bump("cross_gym")
        if payment.membership_id:
            sold = memberships.get(payment.membership_id)
            if (
                sold is None
                or sold.gym_id != payment.gym_id
                or payment.member_id is None
                or sold.member_id != payment.member_id
            ):
                bump("membership_member_mismatch")
            elif (
                payment.entry_type == "CHARGE" and payment.status in {"POSTED", "VOID"}
            ) or (
                payment.entry_type in {"REFUND", "REVERSAL"} and payment.status == "POSTED"
            ):
                sign = (
                    Decimal("-1") if payment.entry_type in {"REFUND", "REVERSAL"} else Decimal("1")
                )
                paid[(payment.gym_id, payment.membership_id)] += sign * Decimal(payment.amount)
        if payment.entry_type == "CHARGE" and payment.original_payment_id is not None:
            bump("contradictory_lifecycle")
        if payment.entry_type == "CHARGE" and payment.reversal_payment_id is not None:
            linked = payment_by_key.get((payment.gym_id, payment.reversal_payment_id))
            if (
                payment.status != "VOID"
                or linked is None
                or linked.entry_type != "REVERSAL"
                or linked.original_payment_id != payment.id
            ):
                bump("contradictory_lifecycle")
        if payment.entry_type == "REFUND":
            original = payment_by_key.get((payment.gym_id, payment.original_payment_id))
            if (
                original is None
                or original.entry_type != "CHARGE"
                or original.status != "POSTED"
                or original.id == payment.id
            ):
                bump("correction_without_original")
        elif payment.entry_type == "REVERSAL":
            original = payment_by_key.get((payment.gym_id, payment.original_payment_id))
            if (
                original is None
                or original.entry_type != "CHARGE"
                or original.status not in {"POSTED", "VOID"}
                or original.id == payment.id
            ):
                bump("correction_without_original")
        _classify_lifecycle(payment, staff_ids=staff_ids, bump=bump)
        linked = receipts_by_payment.get((payment.gym_id, payment.id), [])
        if payment.status not in {"POSTED", "VOID"} and linked:
            bump("contradictory_lifecycle")
        if payment.status == "REJECTED" and linked:
            bump("contradictory_lifecycle")
        for receipt in linked:
            if (
                receipt.amount != payment.amount
                or receipt.currency != payment.currency
                or receipt.method != payment.method
                or receipt.business_date != payment.business_date
            ):
                bump("contradictory_lifecycle")
    reversal_originals: set[tuple[Any, Any]] = set()
    for payment in payments:
        if payment.entry_type != "REVERSAL" or payment.original_payment_id is None:
            continue
        key = (payment.gym_id, payment.original_payment_id)
        if key in reversal_originals:
            bump("contradictory_lifecycle")
        reversal_originals.add(key)

    for (gym_id, membership_id), total in paid.items():
        sold = memberships.get(membership_id)
        if sold is not None and sold.gym_id == gym_id and total > Decimal(sold.final_amount):
            bump("posted_exceeds_obligation")

    planned = _plan_receipts(
        payments=payments,
        receipts=receipts,
        receipts_by_payment=receipts_by_payment,
        gyms=gyms,
        members=members,
        users=users,
        staff_user=staff_user,
        profiles=profiles,
        bump=bump,
    )
    if any(problems.values()):
        detail = ", ".join(f"{name}={count}" for name, count in sorted(problems.items()) if count)
        raise RuntimeError(f"payment reconciliation failed: {detail}")

    for payment in payments:
        if payment.status == "POSTED":
            posted_at = payment.posted_at or payment.approved_at
            posted_by = payment.posted_by_membership_id or payment.approved_by_membership_id
            legacy = bool(payment.legacy_unlinked_cash) or (
                payment.method == "CASH" and payment.cash_transaction_id is None
            )
            if (
                payment.posted_at != posted_at
                or payment.posted_by_membership_id != posted_by
                or bool(payment.legacy_unlinked_cash) != legacy
            ):
                payment.posted_at = posted_at
                payment.posted_by_membership_id = posted_by
                payment.legacy_unlinked_cash = legacy
                payment.save(
                    update_fields=[
                        "posted_at",
                        "posted_by_membership_id",
                        "legacy_unlinked_cash",
                        "updated_at",
                    ]
                )
        elif payment.status == "CANCELLED":
            cancelled_by = payment.updated_by_membership_id
            cancelled_at = payment.cancelled_at or payment.updated_at
            if (
                payment.cancelled_by_membership_id != cancelled_by
                or payment.cancelled_at != cancelled_at
            ):
                payment.cancelled_by_membership_id = cancelled_by
                payment.cancelled_at = cancelled_at
                payment.save(
                    update_fields=["cancelled_by_membership_id", "cancelled_at", "updated_at"]
                )
        elif payment.status == "VOID":
            actor = payment.voided_by_membership_id or payment.updated_by_membership_id
            if payment.voided_by_membership_id != actor:
                payment.voided_by_membership_id = actor
                payment.save(update_fields=["voided_by_membership_id", "updated_at"])

    for payment in payments:
        if payment.entry_type != "REVERSAL" or payment.original_payment_id is None:
            continue
        original = payment_by_key.get((payment.gym_id, payment.original_payment_id))
        if original is None or original.status != "VOID":
            continue
        if original.reversal_payment_id == payment.id:
            continue
        original.reversal_payment_id = payment.id
        original.save(update_fields=["reversal_payment_id", "updated_at"])

    for item in planned:
        if item["kind"] == "existing":
            receipt = item["receipt"]
            if receipt.sequence_key:
                continue
            payment = payment_by_key[(receipt.gym_id, receipt.payment_id)]
            _fill_receipt_snapshot(
                receipt,
                payment=payment,
                gym=gyms[receipt.gym_id],
                member=members.get(payment.member_id),
                sequence_key=item["sequence_key"],
                sequence_value=item["sequence_value"],
                sequence_year=item["sequence_year"],
            )
            if payment.status == "VOID":
                receipt.status = "VOID"
                receipt.voided_at = payment.voided_at
                receipt.voided_by_membership_id = payment.voided_by_membership_id
                receipt.void_reason = payment.void_reason
            receipt.save()
        else:
            payment = item["payment"]
            Receipt.objects.create(
                id=item["id"],
                gym_id=payment.gym_id,
                payment_id=payment.id,
                receipt_number=item["receipt_number"],
                sequence_key=item["sequence_key"],
                sequence_value=item["sequence_value"],
                sequence_year=item["sequence_year"],
                issued_at=payment.approved_at,
                gym_code=item["gym_code"],
                gym_name=item["gym_name"],
                gym_address=item["gym_address"],
                member_number=item["member_number"],
                member_name=item["member_name"],
                description=payment.description,
                amount=payment.amount,
                currency=payment.currency,
                method=payment.method,
                business_date=payment.business_date,
                issuer_name=item["issuer_name"],
                status="ISSUED",
                created_by_membership_id=payment.approved_by_membership_id,
            )


def _posted_columns_match(payment: Any) -> bool:
    if payment.posted_at is None and payment.posted_by_membership_id is None:
        return True
    return (
        payment.posted_at == payment.approved_at
        and payment.posted_by_membership_id == payment.approved_by_membership_id
    )


def _former_charge_evidence(payment: Any, *, staff_ids: set[tuple[Any, Any]], bump: Any) -> None:
    submitter = payment.submitted_by_membership_id
    approver = payment.approved_by_membership_id
    if (
        payment.submitted_at is None
        or submitter is None
        or (payment.gym_id, submitter) not in staff_ids
        or payment.approved_at is None
        or approver is None
        or (payment.gym_id, approver) not in staff_ids
    ):
        bump("missing_lifecycle_evidence")
        return
    if approver == payment.created_by_membership_id or approver == submitter:
        bump("charge_sod_violation")


def _legacy_direct_post(payment: Any, *, staff_ids: set[tuple[Any, Any]], bump: Any) -> None:
    approver = payment.approved_by_membership_id
    creator = payment.created_by_membership_id
    submitted = payment.submitted_at is not None or payment.submitted_by_membership_id is not None
    if (
        payment.approved_at is None
        or approver is None
        or (payment.gym_id, approver) not in staff_ids
        or creator is None
        or (payment.gym_id, creator) not in staff_ids
    ):
        bump("missing_lifecycle_evidence")
    if submitted or not _posted_columns_match(payment):
        bump("contradictory_lifecycle")


def _classify_lifecycle(payment: Any, *, staff_ids: set[tuple[Any, Any]], bump: Any) -> None:
    submitted = payment.submitted_at is not None or payment.submitted_by_membership_id is not None
    approved = payment.approved_at is not None or payment.approved_by_membership_id is not None
    rejected = payment.rejected_at is not None or payment.rejected_by_membership_id is not None
    cancelled = payment.cancelled_at is not None or payment.cancelled_by_membership_id is not None
    posted = payment.posted_at is not None or payment.posted_by_membership_id is not None
    voided = payment.voided_at is not None or payment.voided_by_membership_id is not None
    reasons = (
        str(payment.rejection_reason or "").strip(),
        str(payment.void_reason or "").strip(),
        str(payment.cancellation_reason or "").strip(),
    )
    if payment.status == "DRAFT":
        if submitted or approved or rejected or cancelled or posted or voided or any(reasons):
            bump("contradictory_lifecycle")
        return
    if payment.status == "PENDING_APPROVAL":
        if payment.submitted_at is None or payment.submitted_by_membership_id is None:
            bump("missing_lifecycle_evidence")
        elif (payment.gym_id, payment.submitted_by_membership_id) not in staff_ids:
            bump("missing_lifecycle_evidence")
        if approved or rejected or cancelled or posted or voided or any(reasons):
            bump("contradictory_lifecycle")
        return
    if payment.status == "POSTED":
        if payment.entry_type == "CHARGE":
            _former_charge_evidence(payment, staff_ids=staff_ids, bump=bump)
            if not _posted_columns_match(payment):
                bump("contradictory_lifecycle")
            if rejected or cancelled or voided or any(reasons):
                bump("contradictory_lifecycle")
        elif payment.entry_type in {"REFUND", "REVERSAL"}:
            _legacy_direct_post(payment, staff_ids=staff_ids, bump=bump)
            if rejected or cancelled or voided or any(reasons):
                bump("contradictory_lifecycle")
        else:
            bump("contradictory_lifecycle")
        return
    if payment.status == "REJECTED":
        if (
            payment.rejected_at is None
            or payment.rejected_by_membership_id is None
            or not str(payment.rejection_reason or "").strip()
        ):
            bump("missing_lifecycle_evidence")
        elif (payment.gym_id, payment.rejected_by_membership_id) not in staff_ids:
            bump("missing_lifecycle_evidence")
        if approved or posted or voided or cancelled or str(payment.void_reason or "").strip():
            bump("contradictory_lifecycle")
        return
    if payment.status == "CANCELLED":
        if not str(payment.cancellation_reason or "").strip():
            bump("missing_lifecycle_evidence")
        elif (payment.gym_id, payment.updated_by_membership_id) not in staff_ids:
            bump("missing_lifecycle_evidence")
        if submitted or approved or rejected or posted or voided:
            bump("contradictory_lifecycle")
        if str(payment.rejection_reason or "").strip() or str(payment.void_reason or "").strip():
            bump("contradictory_lifecycle")
        return
    if payment.status == "VOID":
        if payment.entry_type != "CHARGE":
            bump("contradictory_lifecycle")
            return
        _former_charge_evidence(payment, staff_ids=staff_ids, bump=bump)
        if not _posted_columns_match(payment):
            bump("contradictory_lifecycle")
        actor = payment.voided_by_membership_id or payment.updated_by_membership_id
        if (
            payment.voided_at is None
            or not str(payment.void_reason or "").strip()
            or actor is None
            or (payment.gym_id, actor) not in staff_ids
        ):
            bump("missing_lifecycle_evidence")
        if rejected or cancelled or reasons[0] or reasons[2]:
            bump("contradictory_lifecycle")
        return
    bump("contradictory_lifecycle")


def _plan_receipts(
    *,
    payments: list[Any],
    receipts: list[Any],
    receipts_by_payment: dict[tuple[Any, Any], list[Any]],
    gyms: dict[Any, Any],
    members: dict[Any, Any],
    users: dict[Any, Any],
    staff_user: dict[tuple[Any, Any], Any],
    profiles: dict[tuple[Any, Any], Any],
    bump: Any,
) -> list[dict[str, Any]]:
    existing_numbers = {(row.gym_id, row.receipt_number) for row in receipts}
    events: list[dict[str, Any]] = []
    for receipt in receipts:
        if receipt.sequence_key:
            continue
        events.append(
            {
                "kind": "existing",
                "gym_id": receipt.gym_id,
                "issued_at": receipt.issued_at,
                "sort_id": str(receipt.id),
                "receipt": receipt,
            }
        )
    for payment in payments:
        if payment.status != "POSTED":
            continue
        if receipts_by_payment.get((payment.gym_id, payment.id)):
            continue
        receipt_id = uuid.uuid5(_NAMESPACE, f"{payment.gym_id}:{payment.id}")
        issuer = _issuer_name(
            users=users,
            staff_user=staff_user,
            profiles=profiles,
            gym_id=payment.gym_id,
            membership_id=payment.approved_by_membership_id,
        )
        member = members.get(payment.member_id) if payment.member_id else None
        member_number, member_name = _member_snapshot(member)
        if issuer is None or payment.approved_at is None:
            bump("issuer_name_unusable")
            continue
        if len(issuer) > 201 or len(member_name) > 201 or len(member_number) > 32:
            bump("issuer_name_unusable")
            continue
        gym = gyms.get(payment.gym_id)
        if gym is None or not gym.code or not gym.name:
            bump("issuer_name_unusable")
            continue
        events.append(
            {
                "kind": "create",
                "gym_id": payment.gym_id,
                "issued_at": payment.approved_at,
                "sort_id": str(receipt_id),
                "id": receipt_id,
                "payment": payment,
                "issuer_name": issuer,
                "member_number": member_number,
                "member_name": member_name,
                "gym_code": gym.code,
                "gym_name": gym.name,
                "gym_address": _gym_address(gym),
            }
        )
    events.sort(key=lambda item: (str(item["gym_id"]), item["issued_at"], item["sort_id"]))
    next_value: dict[tuple[Any, str], int] = defaultdict(int)
    for receipt in receipts:
        if not receipt.sequence_key or receipt.sequence_value is None:
            continue
        key = (receipt.gym_id, receipt.sequence_key)
        next_value[key] = max(next_value[key], int(receipt.sequence_value))
    planned: list[dict[str, Any]] = []
    for item in events:
        year = item["issued_at"].year
        sequence_key = f"LEGACY:{year}"
        slot = (item["gym_id"], sequence_key)
        next_value[slot] += 1
        value = next_value[slot]
        item["sequence_key"] = sequence_key
        item["sequence_value"] = value
        item["sequence_year"] = year
        if item["kind"] == "create":
            number = f"LEGACY-{year}-{value:06d}"
            if (item["gym_id"], number) in existing_numbers or len(number) > 64:
                bump("legacy_number_collision")
                continue
            existing_numbers.add((item["gym_id"], number))
            item["receipt_number"] = number
        planned.append(item)
    return planned


def _issuer_name(
    *,
    users: dict[Any, Any],
    staff_user: dict[tuple[Any, Any], Any],
    profiles: dict[tuple[Any, Any], Any],
    gym_id: Any,
    membership_id: Any,
) -> str | None:
    user_id = staff_user.get((gym_id, membership_id))
    user = users.get(user_id)
    if user is None:
        return None
    personal = f"{user.first_name} {user.last_name}".strip()
    if personal:
        return personal
    display = str(profiles.get((gym_id, membership_id)) or "").strip()
    if display:
        return display
    email = str(getattr(user, "email", "") or "").strip()
    return email or None


def _member_snapshot(member: Any) -> tuple[str, str]:
    if member is None:
        return "Walk-in", "Walk-in"
    name = f"{member.first_name} {member.last_name}".strip()
    return member.member_number, name


def _gym_address(gym: Any) -> str:
    parts = [
        gym.address_line1,
        gym.address_line2,
        gym.city,
        gym.region,
        gym.postal_code,
        gym.country_code,
    ]
    return ", ".join(part for part in parts if part)[:800]


def _fill_receipt_snapshot(
    receipt: Any,
    *,
    payment: Any,
    gym: Any,
    member: Any,
    sequence_key: str,
    sequence_value: int,
    sequence_year: int,
) -> None:
    member_number, member_name = _member_snapshot(member)
    receipt.sequence_key = sequence_key
    receipt.sequence_value = sequence_value
    receipt.sequence_year = sequence_year
    receipt.gym_code = gym.code
    receipt.gym_name = gym.name
    receipt.gym_address = _gym_address(gym)
    if not receipt.member_number:
        receipt.member_number = member_number
    if not receipt.member_name:
        receipt.member_name = member_name


class Migration(migrations.Migration):
    dependencies = [("members", "0015_payment_receipt_expand")]
    operations = [migrations.RunPython(reconcile_legacy_payments, migrations.RunPython.noop)]

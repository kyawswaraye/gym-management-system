"""Canonical member name, contact, and address normalization.

These helpers are pure so create, update, and model saves persist the same
values. Phone and email are stored only in normalized form; blank input
becomes null for those two fields. Migration 0007 keeps its own copy for
the historical backfill.
"""

from __future__ import annotations

from typing import Any

ADDRESS_KEYS = (
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country_code",
)
MEMBER_GENDERS = frozenset({"FEMALE", "MALE", "OTHER", "UNSPECIFIED"})


def normalize_member_name(value: str) -> str:
    return " ".join(value.split())


def normalize_full_name(first_name: str, last_name: str) -> str:
    return " ".join(part.casefold() for part in (first_name, last_name) if part)


def normalize_member_email(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def normalize_member_phone(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    leading_plus = cleaned.startswith("+")
    body = cleaned[1:] if leading_plus else cleaned
    digits = "".join(character for character in body if character not in " ().-")
    if not digits.isdigit():
        raise ValueError("Enter a phone number using digits and an optional leading +.")
    canonical = f"+{digits}" if leading_plus else digits
    if len(canonical) > 32:
        raise ValueError("Enter a phone number using digits and an optional leading +.")
    return canonical


def normalize_member_gender(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper().replace("-", " ")
    cleaned = "_".join(part for part in cleaned.split() if part)
    if not cleaned:
        return None
    if cleaned not in MEMBER_GENDERS:
        raise ValueError("Select a valid gender.")
    return cleaned


def canonical_address(value: object) -> dict[str, str]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Enter a structured address.")
    unknown = set(value) - set(ADDRESS_KEYS)
    if unknown:
        raise ValueError("Address contains unknown fields.")
    result = {key: "" for key in ADDRESS_KEYS}
    for key in ADDRESS_KEYS:
        raw = value.get(key, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise ValueError("Address fields must be text.")
        if key == "country_code":
            code = raw.strip().upper()
            if code and (len(code) != 2 or not code.isalpha()):
                raise ValueError("Enter a two-letter country code.")
            result[key] = code
        else:
            result[key] = " ".join(raw.split())
    return result


def address_for_response(value: object) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    try:
        return canonical_address(raw)
    except ValueError:
        result = {key: "" for key in ADDRESS_KEYS}
        for key in ADDRESS_KEYS:
            item = raw.get(key, "")
            result[key] = item if isinstance(item, str) else ""
        return result


def manual_sequence_value(prefix: str, member_number: str) -> int | None:
    lead = f"{prefix}-"
    if not member_number.startswith(lead):
        return None
    suffix = member_number[len(lead) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


def apply_member_normalization(member: Any) -> None:
    member.first_name = normalize_member_name(str(member.first_name or ""))
    member.last_name = normalize_member_name(str(member.last_name or ""))
    member.normalized_full_name = normalize_full_name(member.first_name, member.last_name)
    member.phone = normalize_member_phone(member.phone)
    member.email = normalize_member_email(member.email)
    member.gender = normalize_member_gender(member.gender)
    member.emergency_contact_name = normalize_member_name(str(member.emergency_contact_name or ""))
    member.emergency_contact_phone = normalize_member_phone(member.emergency_contact_phone) or ""
    member.address = canonical_address(member.address)

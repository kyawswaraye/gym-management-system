"""Resolve the names staff say into the ids a write already stores.

A model may send a display name, or an id copied from a different record.
The stored payload stays an id. The confirmation card shows the name.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.assistant.models import Conversation, Message
from apps.classes.models import ClassBooking, ClassOccurrence, ClassType
from apps.common.db.tenant_context import set_gym_context
from apps.inventory.models import Product, StockMovement
from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.members.services import inclusive_expiry_date
from apps.tenants.models import Gym, GymMembership, StaffProfile
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    ServicePackage,
    TrainerAssignment,
)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YMD = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_DMY = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")
_MATCH_LIMIT = 8
_EN_MONTHS: tuple[tuple[str, int], ...] = (
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
)
_MY_MONTHS: tuple[tuple[str, int], ...] = (
    ("ဇန်နဝါရီ", 1),
    ("ဖေဖော်ဝါရီ", 2),
    ("မတ်", 3),
    ("ဧပြီ", 4),
    ("မေ", 5),
    ("ဇွန်", 6),
    ("ဇူလိုင်", 7),
    ("ဩဂုတ်", 8),
    ("စက်တင်ဘာ", 9),
    ("အောက်တိုဘာ", 10),
    ("နိုဝင်ဘာ", 11),
    ("ဒီဇင်ဘာ", 12),
)
_MONTH_NUMBERS = {name.casefold(): number for name, number in (*_EN_MONTHS, *_MY_MONTHS)}
_MONTH_NAME = "|".join(
    re.escape(name)
    for name, _number in sorted(_MONTH_NUMBERS.items(), key=lambda item: len(item[0]), reverse=True)
)
_MONTH_WORD = rf"(?<![A-Za-z\u1000-\u109F])(?:{_MONTH_NAME})(?![A-Za-z\u1000-\u109F])"
_DAY_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*({_MONTH_WORD})(?:\s+(\d{{4}}))?(?!\d)",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    rf"({_MONTH_WORD})\s*(\d{{1,2}})(?:(?:st|nd|rd|th)?\s*,?\s*(\d{{4}}))?(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NamedRecord:
    id: str
    name: str
    when: str = ""
    label: str = ""
    code: str = ""

    def shown(self) -> str:
        base = self.label or self.name
        if self.code and self.code not in base:
            return f"{base} ({self.code})"
        return base


@dataclass(frozen=True)
class Choice:
    id: str = ""
    label: str = ""
    error: str = ""
    matches: tuple[NamedRecord, ...] = ()


def choose_reference(supplied: str, records: Sequence[NamedRecord], staff_text: str) -> Choice:
    """Pick the record the staff named, or say the name is missing or ambiguous."""

    coded = _by_code(records, staff_text)
    if len(coded) == 1:
        return Choice(id=coded[0].id, label=coded[0].shown())
    if len(coded) > 1:
        return Choice(error="ambiguous", matches=tuple(coded[:_MATCH_LIMIT]))
    named = _mentioned(records, staff_text)
    dates = _explicit_iso_dates(f"{staff_text}\n{supplied}")
    parsed = _uuid_text(supplied)
    if parsed is not None:
        current = next((record for record in records if record.id == parsed), None)
        if current is None:
            return Choice(error="not_found")
        pool = _dated(named, dates) if named else []
        coded = _by_code(pool, staff_text)
        if len(coded) == 1:
            return Choice(id=coded[0].id, label=coded[0].shown())
        if _ambiguous_named(pool):
            return Choice(error="ambiguous", matches=tuple(pool[:_MATCH_LIMIT]))
        if _needs_a_date(current, records, staff_text, dates):
            return Choice(
                error="ambiguous",
                matches=tuple(_same_name(current, records)[:_MATCH_LIMIT]),
            )
        if not pool or any(record.id == current.id for record in pool):
            return Choice(id=current.id, label=current.shown())
        if len(pool) == 1:
            return Choice(id=pool[0].id, label=pool[0].shown())
        return Choice(error="ambiguous", matches=tuple(pool[:_MATCH_LIMIT]))

    supplied_codes = _by_code(records, supplied)
    if supplied_codes:
        mentioned = _mentioned(records, staff_text)
        if _ambiguous_named(mentioned):
            return Choice(error="ambiguous", matches=tuple(mentioned[:_MATCH_LIMIT]))
        if len(mentioned) == 1:
            return Choice(id=mentioned[0].id, label=mentioned[0].shown())
        if len(supplied_codes) == 1:
            return Choice(id=supplied_codes[0].id, label=supplied_codes[0].shown())
    hits = _dated(_name_hits(records, supplied), dates)
    coded = _by_code(hits, staff_text)
    if len(coded) == 1:
        hits = coded
    if len(hits) == 1:
        return Choice(id=hits[0].id, label=hits[0].shown())
    if not hits:
        return Choice(error="not_found")
    return Choice(error="ambiguous", matches=tuple(hits[:_MATCH_LIMIT]))


def resolve_references(
    gym: Gym,
    conversation: Conversation,
    payload: Mapping[str, object],
    permission_codes: frozenset[str] | None = None,
    stored_date: str = "",
    require_join: bool = False,
) -> tuple[dict[str, object], dict[str, str], dict[str, object] | None]:
    """Return the id payload, card labels, and a tool error when staff must choose."""

    present = {
        field: value
        for field, value in ((field, payload.get(field)) for field in _LOADERS)
        if isinstance(value, str) and value.strip()
    }
    line_products = _line_product_values(payload)
    denied = _denied_catalog(present, line_products, permission_codes or frozenset())
    if denied is not None:
        return dict(payload), {}, {"error": "permission_denied", "field": denied}
    resolved = dict(payload)
    needed = set(present)
    if line_products:
        needed.add("product_id")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        staff_bodies = _staff_bodies(gym, conversation)
        staff_text = staff_bodies[0] if staff_bodies else ""
        _normalize_numbers(resolved)
        _normalize_line_numbers(resolved)
        unstated = _keep_stated_dates(
            resolved,
            staff_text,
            today=_local_today(gym),
            gym=gym,
            history=staff_bodies[1:],
            stored=stored_date,
            require_join=require_join,
        )
        if unstated is not None:
            if unstated.startswith("time:"):
                return dict(payload), {}, {"error": "time_unconfirmed", "field": unstated[5:]}
            problem = _date_problem_body(unstated, stored_date)
            if problem is not None:
                return dict(payload), {}, problem
            return dict(payload), {}, {"error": "date_unconfirmed", "field": unstated}
        if not needed:
            return resolved, {}, None
        loaded = {field: loader(gym) for field, loader in _LOADERS.items() if field in needed}
    labels: dict[str, str] = {}
    for field, supplied in present.items():
        choice = choose_reference(supplied, loaded[field], staff_text)
        failure = _choice_failure(field, choice)
        if failure is not None:
            return dict(payload), {}, failure
        resolved[field] = choice.id
        labels[field] = choice.label
    if line_products:
        copied, line_labels, failure = _resolve_line_products(
            payload,
            loaded["product_id"],
            staff_text,
        )
        if failure is not None:
            return dict(payload), {}, failure
        resolved["lines"] = copied
        labels.update(line_labels)
    return resolved, labels, None


_DATE_FIELDS = (
    "service_date",
    "effective_from",
    "starts_at",
    "ends_at",
    "start_date",
    "join_date",
    "business_date",
)
_CLOCK_FIELDS = ("starts_at", "ends_at")
_MONEY = Decimal("0.01")
_UNMETERED_TOOLS = frozenset({"create_pt_booking", "create_class_booking"})
_PAYMENT_AMOUNT_TOOLS = frozenset({"create_payment", "refund_payment", "update_payment"})
_NUMERIC_FIELDS = frozenset(
    {
        "amount",
        "gross_fee",
        "price",
        "opening_cash",
        "actual_closing",
        "sale_price",
        "current_unit_cost",
        "default_unit_cost",
        "quantity",
    }
)
_MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _history_date_is_a_lookup(text: str) -> bool:
    """A question about records does not choose a later write's date."""

    from apps.assistant.knowledge import information_question

    return information_question(text)


def _keep_stated_dates(
    payload: dict[str, object],
    staff_text: str,
    today: date | None = None,
    gym: Gym | None = None,
    history: Sequence[str] = (),
    stored: str = "",
    require_join: bool = False,
) -> str | None:
    """Rewrite a date or clock the staff stated. Leave an unstated value unchanged."""

    anchor = today or timezone.localdate()
    current = _stated_day(staff_text, anchor)
    stated = current
    if stated is None:
        for earlier in history:
            if _history_date_is_a_lookup(earlier):
                continue
            earlier_day = _stated_day(earlier, anchor)
            if earlier_day == "conflict":
                stated = "conflict"
                break
            if isinstance(earlier_day, date):
                stated = earlier_day
                break
    if stated is None:
        stated = _iso_day(stored)
    if current in {"ambiguous", "invalid", "conflict"} and _payload_has_date(payload):
        return _encoded_date_problem(staff_text, str(current))
    _normalize_payload_dates(payload)
    clocks = _stated_clocks(staff_text)
    for field in _DATE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str):
            continue
        raw = value.strip()
        if len(raw) < 10:
            continue
        try:
            date.fromisoformat(raw[:10])
        except ValueError:
            if isinstance(stated, date) and field not in _CLOCK_FIELDS:
                payload[field] = stated.isoformat()
            continue
        if field == "join_date":
            if isinstance(stated, date):
                if raw[:10] != stated.isoformat():
                    replacement = stated.isoformat()
                    payload[field] = replacement if len(raw) == 10 else replacement + raw[10:]
                continue
            if require_join or _registration_names_a_plan(staff_text):
                return field
            continue
        if not isinstance(stated, date):
            return field
        if field in _CLOCK_FIELDS and not clocks:
            return f"time:{field}"
        if field in _CLOCK_FIELDS and "T" in raw and gym is not None:
            shifted = _shift_local_day(raw, stated, gym)
            if shifted is None:
                return field
            payload[field] = shifted
            continue
        if raw[:10] == stated.isoformat():
            continue
        payload[field] = stated.isoformat() if len(raw) == 10 else stated.isoformat() + raw[10:]
    return _align_clocks(payload, clocks, gym)


@dataclass(frozen=True)
class _CalendarHit:
    kind: str
    token: str
    day: date | None = None
    first: date | None = None
    second: date | None = None


def calendar_day(text: str, today: date) -> date | str | None:
    """Return the one calendar day named in text, or ambiguous, invalid, or conflict."""

    return _stated_day(text, today)


def _stated_day(text: str, today: date) -> date | str | None:
    hits = _calendar_hits(text.translate(_MYANMAR_DIGITS))
    found = [hit.day for hit in hits if hit.kind == "date" and hit.day is not None]
    unique = list(dict.fromkeys(found))
    if len(unique) > 1:
        return "conflict"
    if len(unique) == 1:
        return unique[0]
    if any(hit.kind == "ambiguous" for hit in hits):
        return "ambiguous"
    if any(hit.kind == "invalid" for hit in hits):
        return "invalid"
    offsets: set[int] = set()
    folded = text.casefold()
    if re.search(r"\btoday\b", folded):
        offsets.add(0)
    if re.search(r"\btomorrow\b", folded):
        offsets.add(1)
    if re.search(r"\byesterday\b", folded):
        offsets.add(-1)
    if re.search(r"\bnext week\b", folded):
        offsets.add(7)
    for token, offset in (
        ("ဒီနေ့", 0),
        ("ယနေ့", 0),
        ("မနက်ဖြန်", 1),
        ("နက်ဖြန်", 1),
        ("နောက်တစ်နေ့", 1),
        ("နောက်တစ်ရက်", 1),
        ("နောက်နေ့", 1),
        ("မနေ့", -1),
        ("ယမန်နေ့", -1),
        ("နောက်တစ်ပတ်", 7),
        ("နောက်အပတ်", 7),
    ):
        if token in text:
            offsets.add(offset)
    if len(offsets) > 1:
        return "conflict"
    if len(offsets) == 1:
        return today + timedelta(days=next(iter(offsets)))
    return None


def _calendar_hits(text: str) -> list[_CalendarHit]:
    hits: list[_CalendarHit] = []
    occupied: list[tuple[int, int]] = []
    for match in _YMD.finditer(text):
        hits.append(_ymd_hit(match))
        occupied.append(match.span())
    for match in _DMY.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        hits.append(_dmy_hit(match))
        occupied.append(match.span())
    for pattern in (_DAY_MONTH, _MONTH_DAY):
        for match in pattern.finditer(text):
            if _overlaps(match.span(), occupied):
                continue
            hits.append(_month_hit(match, pattern is _DAY_MONTH))
            occupied.append(match.span())
    return hits


def _ymd_hit(match: re.Match[str]) -> _CalendarHit:
    parsed = _real_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if parsed is None:
        return _CalendarHit("invalid", match.group(0))
    return _CalendarHit("date", match.group(0), parsed)


def _dmy_hit(match: re.Match[str]) -> _CalendarHit:
    first = int(match.group(1))
    second = int(match.group(2))
    year = int(match.group(3))
    token = match.group(0)
    day_first = _real_date(year, second, first)
    month_first = _real_date(year, first, second)
    if day_first is not None and month_first is not None and day_first != month_first:
        return _CalendarHit("ambiguous", token, first=day_first, second=month_first)
    if day_first is not None:
        return _CalendarHit("date", token, day_first)
    if month_first is not None:
        return _CalendarHit("date", token, month_first)
    return _CalendarHit("invalid", token)


def _month_hit(match: re.Match[str], day_first: bool) -> _CalendarHit:
    if day_first:
        day = int(match.group(1))
        month_name = match.group(2)
        year_text = match.group(3)
    else:
        month_name = match.group(1)
        day = int(match.group(2))
        year_text = match.group(3)
    month = _MONTH_NUMBERS.get(month_name.casefold())
    token = match.group(0).strip()
    if month is None:
        return _CalendarHit("invalid", token)
    if not year_text:
        return _CalendarHit("ambiguous", token)
    parsed = _real_date(int(year_text), month, day)
    if parsed is None:
        return _CalendarHit("invalid", token)
    return _CalendarHit("date", token, parsed)


def _real_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(start < span[1] and span[0] < end for start, end in occupied)


def _explicit_iso_dates(text: str) -> set[str]:
    return {
        hit.day.isoformat()
        for hit in _calendar_hits(text.translate(_MYANMAR_DIGITS))
        if hit.kind == "date" and hit.day is not None
    }


def _without_calendar_text(text: str) -> str:
    cleaned = _YMD.sub(" ", text)
    cleaned = _DMY.sub(" ", cleaned)
    cleaned = _DAY_MONTH.sub(" ", cleaned)
    return _MONTH_DAY.sub(" ", cleaned)


def _iso_day(value: str) -> date | None:
    if len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


_PLAN_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")


def _registration_names_a_plan(text: str) -> bool:
    """A membership sale in this message needs the join date from the staff, not the model."""

    folded = text.casefold()
    if _PLAN_TOKEN.search(text):
        return True
    return "membership" in folded or "အသင်းဝင်" in text or "ဝယ်" in text


def _payload_has_date(payload: Mapping[str, object]) -> bool:
    return any(
        isinstance(payload.get(field), str) and str(payload.get(field)).strip()
        for field in _DATE_FIELDS
    )


def _encoded_date_problem(text: str, kind: str) -> str:
    hits = _calendar_hits(text.translate(_MYANMAR_DIGITS))
    if kind == "conflict":
        tokens = [hit.token for hit in hits if hit.kind == "date" and hit.token]
        return "conflict:" + " ".join(tokens)
    for hit in hits:
        if hit.kind != kind:
            continue
        if kind == "ambiguous" and hit.first is not None and hit.second is not None:
            return f"ambiguous:{hit.token}:{hit.first.isoformat()}:{hit.second.isoformat()}"
        return f"{kind}:{hit.token}"
    return kind


def _date_problem_body(unstated: str, stored: str) -> dict[str, object] | None:
    if unstated.startswith("invalid:"):
        error = "date_invalid"
    elif unstated.startswith(("ambiguous:", "conflict:")):
        error = "date_ambiguous"
    else:
        return None
    parts = unstated.split(":")
    body: dict[str, object] = {"error": error, "token": parts[1] if len(parts) > 1 else ""}
    if error == "date_ambiguous" and len(parts) >= 4:
        body["first"] = parts[2]
        body["second"] = parts[3]
    kept = _iso_day(stored)
    if kept is not None:
        body["stored"] = kept.isoformat()
    return body


def _normalize_payload_dates(payload: dict[str, object]) -> None:
    for field in _DATE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            payload[field] = _replace_unambiguous_day(value)


def _replace_unambiguous_day(raw: str) -> str:
    converted = raw.strip().translate(_MYANMAR_DIGITS)
    match = _YMD.match(converted) or _DMY.match(converted)
    if match is None:
        return raw.strip()
    hit = _ymd_hit(match) if match.re is _YMD else _dmy_hit(match)
    if hit.kind != "date" or hit.day is None:
        return raw.strip()
    return hit.day.isoformat() + converted[match.end() :]


def _stated_time(text: str) -> bool:
    return bool(_stated_clocks(text))


def _local_today(gym: Gym) -> date:
    return timezone.now().astimezone(_gym_zone(gym)).date()


def _gym_zone(gym: Gym | None) -> ZoneInfo:
    name = getattr(gym, "timezone", None) if gym is not None else None
    if not isinstance(name, str) or not name:
        name = "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        return ZoneInfo("UTC")


def _stated_clocks(text: str) -> tuple[tuple[int, int], ...]:
    """Return clock times the staff actually wrote, in order."""

    converted = text.translate(_MYANMAR_DIGITS)
    cleaned = _without_calendar_text(converted)
    found: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    patterns = (
        re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)"),
        re.compile(r"(?<!\d)(\d{1,2})\s*(am|pm)\b", re.IGNORECASE),
        re.compile(r"(?<!\d)(\d{1,2})\s*နာရီ\s*ခွဲ"),
        re.compile(r"(?<!\d)(\d{1,2})\s*နာရီ(?:\s*(\d{1,2})\s*မိနစ်)?"),
    )
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            span = match.span()
            if any(start <= span[0] < end or start < span[1] <= end for start, end in occupied):
                continue
            hour = int(match.group(1))
            minute_text = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            if pattern is patterns[1]:
                minute = 0
                mark = str(minute_text).casefold()
                if hour == 12:
                    hour = 0
                if mark == "pm":
                    hour += 12
            elif pattern is patterns[2]:
                minute = 30
            else:
                minute = int(minute_text) if minute_text and str(minute_text).isdigit() else 0
                if pattern is patterns[0]:
                    tail = cleaned[match.end() : match.end() + 4]
                    if re.match(r"\s*pm\b", tail, re.IGNORECASE) and hour < 12:
                        hour += 12
                    elif re.match(r"\s*am\b", tail, re.IGNORECASE) and hour == 12:
                        hour = 0
            if hour > 23 or minute > 59:
                continue
            occupied.append(span)
            found.append((hour, minute))
    return tuple(found)


def _align_clocks(
    payload: dict[str, object],
    clocks: tuple[tuple[int, int], ...],
    gym: Gym | None,
) -> str | None:
    """Replace a model clock with the clock the staff stated."""

    present = [
        field
        for field in _CLOCK_FIELDS
        if isinstance(payload.get(field), str) and "T" in str(payload.get(field))
    ]
    if not present:
        return None
    if not clocks:
        return f"time:{present[0]}"
    if "starts_at" in present:
        rewritten = _with_clock(str(payload["starts_at"]), clocks[0], gym)
        if rewritten is None:
            return "time:starts_at"
        payload["starts_at"] = rewritten
    if "ends_at" not in present:
        return None
    if len(clocks) < 2:
        return "time:ends_at"
    rewritten = _with_clock(str(payload["ends_at"]), clocks[1], gym)
    if rewritten is None:
        return "time:ends_at"
    payload["ends_at"] = rewritten
    return None


def _with_clock(raw: str, clock: tuple[int, int], gym: Gym | None) -> str | None:
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_gym_zone(gym))
    zone = _gym_zone(gym) if gym is not None else parsed.tzinfo
    if zone is None:
        return None
    local = parsed.astimezone(zone).replace(
        hour=clock[0],
        minute=clock[1],
        second=0,
        microsecond=0,
    )
    return local.isoformat()


def _shift_local_day(raw: str, stated: date, gym: Gym) -> str | None:
    """Move a timestamp onto the gym-local day the staff stated."""

    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    zone = _gym_zone(gym)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    local = parsed.astimezone(zone)
    if local.date() == stated:
        return raw
    moved = local.replace(year=stated.year, month=stated.month, day=stated.day)
    return moved.isoformat()


def _normalize_numbers(payload: dict[str, object]) -> None:
    for key, value in payload.items():
        if key not in _NUMERIC_FIELDS or not isinstance(value, str):
            continue
        converted = _normalized_number(value)
        if converted is not None:
            payload[key] = converted


def _normalize_line_numbers(payload: dict[str, object]) -> None:
    """Normalize Myanmar digits inside retail line quantity and discount."""

    lines = payload.get("lines")
    if not isinstance(lines, list):
        return
    copied: list[object] = []
    for line in lines:
        if not isinstance(line, dict):
            copied.append(line)
            continue
        item = dict(line)
        for key in ("quantity", "discount"):
            value = item.get(key)
            if isinstance(value, str):
                converted = _normalized_number(value)
                if converted is not None:
                    item[key] = converted
        copied.append(item)
    payload["lines"] = copied


def _normalized_number(value: str) -> str | None:
    converted = value.translate(_MYANMAR_DIGITS).replace(",", "").replace("၊", "").replace(" ", "")
    if _NUMBER.fullmatch(converted):
        return converted
    return None


_ASSIGNED_ON = {
    "create_pt_sale": "service_date",
    "create_pt_booking": "starts_at",
}


def assignment_gap(
    gym: Gym,
    tool_name: str,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return the assignment a PT sale or booking still needs."""

    field = _ASSIGNED_ON.get(tool_name)
    if field is None:
        return None
    trainer = _uuid_value(payload.get("trainer_staff_profile_id"))
    member = _uuid_text(payload.get("member_id"))
    day = _service_day(payload.get(field), gym)
    if trainer is None or member is None or day is None:
        return None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        covered = (
            TrainerAssignment.objects.filter(
                gym=gym,
                trainer_staff_profile_id=trainer,
                member_id=member,
                status=TrainerAssignment.Status.ACTIVE,
                effective_from__lte=day,
            )
            .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=day))
            .exists()
        )
    if covered:
        return None
    return {
        "error": "assignment_required",
        "member_id": member,
        "trainer_staff_profile_id": str(trainer),
        "effective_from": day.isoformat(),
    }


def existing_assignment(
    gym: Gym,
    tool_name: str,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Refuse a second assignment that overlaps one already saved."""

    if tool_name != "create_assignment":
        return None
    trainer = _uuid_value(payload.get("trainer_staff_profile_id"))
    member = _uuid_text(payload.get("member_id"))
    start = _service_day(payload.get("effective_from"), gym)
    if trainer is None or member is None or start is None:
        return None
    until = _service_day(payload.get("effective_until"), gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = TrainerAssignment.objects.filter(
            gym=gym,
            trainer_staff_profile_id=trainer,
            member_id=member,
            status=TrainerAssignment.Status.ACTIVE,
        ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=start))
        if until is not None:
            rows = rows.filter(effective_from__lte=until)
        covered = rows.exists()
    if not covered:
        return None
    return {"status": "already_saved", "effective_from": start.isoformat()}


def booking_package_gap(
    gym: Gym,
    tool_name: str,
    payload: dict[str, object],
) -> dict[str, object] | None:
    """A metered PT booking needs one active package with sessions left."""

    if tool_name != "create_pt_booking" or _is_unmetered(payload.get("unmetered")):
        return None
    member = _uuid_text(payload.get("member_id"))
    day = _service_day(payload.get("starts_at"), gym)
    if member is None or day is None:
        return None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = MemberServicePackage.objects.filter(
            gym=gym,
            member_id=member,
            package_type="PT",
            status=MemberServicePackage.Status.ACTIVE,
            valid_from__lte=day,
            valid_until__gte=day,
        ).annotate(remaining=Sum("ledger_entries__session_delta"))
        eligible = [row for row in rows if (row.remaining or 0) > 0]
    if len(eligible) == 1:
        payload["member_service_package_id"] = str(eligible[0].id)
        return None
    if _uuid_text(payload.get("member_service_package_id")) in {str(row.id) for row in eligible}:
        return None
    return {"status": "package_required", "member_id": member}


def _is_unmetered(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().casefold() == "true")


def _service_day(value: object, gym: Gym | None = None) -> date | None:
    """Return the gym-local calendar day. A date-only value is already local."""

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "T" not in raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            replaced = _replace_unambiguous_day(raw)
            if replaced == raw:
                return None
            try:
                return date.fromisoformat(replaced[:10])
            except ValueError:
                return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    zone = _gym_zone(gym)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone).date()


def _latest_staff_text(gym: Gym, conversation: Conversation) -> str:
    bodies = _staff_bodies(gym, conversation)
    return bodies[0] if bodies else ""


def _staff_bodies(gym: Gym, conversation: Conversation) -> list[str]:
    rows = Message.objects.filter(
        gym=gym,
        conversation=conversation,
        role=Message.Role.STAFF,
    ).order_by("-created_at", "-id")[:12]
    return [row.body for row in rows if row.body.strip()]


def relative_day(text: str, today: date) -> date | None:
    """Return one gym-local day named in the text. A conflict returns none."""

    stated = _stated_day(text, today)
    return stated if isinstance(stated, date) else None


def relative_window(text: str, today: date) -> tuple[date, date] | None:
    """Return an inclusive gym-local range for a month or week phrase."""

    import calendar

    if "ဒီလ" in text or "this month" in text.casefold():
        last = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, 1), date(today.year, today.month, last)
    if "နောက်လ" in text or "next month" in text.casefold():
        year = today.year + 1 if today.month == 12 else today.year
        month = 1 if today.month == 12 else today.month + 1
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)
    if "နောက်တစ်ပတ်" in text or "next week" in text.casefold():
        start = today + timedelta(days=7)
        return start, start + timedelta(days=6)
    return None


def _members(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = Member.objects.filter(gym=gym).exclude(status=Member.Status.ARCHIVED)
    return tuple(
        NamedRecord(
            str(row.id),
            row.normalized_full_name,
            label=" ".join(part for part in (row.first_name, row.last_name) if part),
            code=row.member_number,
        )
        for row in rows
    )


def _trainers(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = StaffProfile.objects.filter(gym=gym).exclude(
        status__in=(StaffProfile.Status.ARCHIVED, StaffProfile.Status.TERMINATED)
    )
    return tuple(
        NamedRecord(
            str(row.id),
            row.display_name,
            label=row.display_name,
            code=row.staff_code,
        )
        for row in rows
    )


def _class_types(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = ClassType.objects.filter(gym=gym, is_active=True)
    return tuple(NamedRecord(str(row.id), row.name, label=row.name) for row in rows)


def _products(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = Product.objects.filter(gym=gym).order_by("sku", "id")
    return tuple(NamedRecord(str(row.id), row.name, label=row.name, code=row.sku) for row in rows)


def _plans(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = MembershipPlan.objects.filter(gym=gym).order_by("code", "id")
    return tuple(NamedRecord(str(row.id), row.name, label=row.name, code=row.code) for row in rows)


def _service_packages(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = ServicePackage.objects.filter(gym=gym).order_by("code", "id")
    return tuple(NamedRecord(str(row.id), row.name, label=row.name, code=row.code) for row in rows)


@dataclass(frozen=True)
class PackageChoice:
    status: str
    package: ServicePackage | None = None
    matches: tuple[ServicePackage, ...] = ()


_PACKAGE_CODE = re.compile(r"(?<![a-z0-9_])[a-z]{1,12}\d+[a-z0-9]*")


def remember_sale_package(
    gym: Gym,
    conversation: Conversation,
    state: dict[str, str],
) -> None:
    """Keep one identified PT package on the conversation. Never guess from a description."""

    choice = sale_package_choice(gym, conversation, currency=str(gym.currency))
    if choice.status == "one" and choice.package is not None:
        _remember_package(state, choice.package)
        if not state.get("workflow"):
            state["workflow"] = "pt"
        return
    if choice.status in {"ambiguous", "unavailable", "currency_mismatch"}:
        state["package_id"] = ""
        state["package_code"] = ""
        state["package_name"] = ""


def attach_sale_package(
    gym: Gym,
    conversation: Conversation,
    payload: dict[str, object],
    state: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, object] | None]:
    """Put one eligible package id on a PT sale, or ask when it is not unique."""

    currency = _sale_currency(payload, gym)
    supplied = _uuid_value(payload.get("service_package_id"))
    choice = sale_package_choice(gym, conversation, currency=currency)
    if supplied is not None:
        if sale_package_failure(gym, payload) is not None:
            return {}, {"error": "package_unavailable"}
        if choice.status == "one" and choice.package is not None and choice.package.id != supplied:
            named = choice.package
            other = _package_by_id(gym, supplied)
            return {}, _package_ambiguous((named, other) if other is not None else (named,))
        if choice.status == "ambiguous":
            return {}, _package_ambiguous(choice.matches)
        package = _package_by_id(gym, supplied)
        if package is None:
            return {}, {"error": "package_unavailable"}
        _remember_package(state, package)
        return _package_label(package), None
    if choice.status == "one" and choice.package is not None:
        return _bind_package(payload, state, choice.package), None
    if choice.status == "ambiguous":
        return {}, _package_ambiguous(choice.matches)
    if choice.status == "currency_mismatch":
        return {}, {"error": "currency_mismatch"}
    if choice.status == "unavailable":
        return {}, {"error": "package_unavailable"}
    stored = _eligible_stored_package(gym, state, currency)
    if stored is not None:
        return _bind_package(payload, state, stored), None
    if _conversation_names_package(gym, conversation) or _description_points_at_package(
        gym, payload
    ):
        return {}, {"error": "missing_fields", "fields": ["service_package_id"]}
    return {}, None


def sale_package_failure(gym: Gym, payload: Mapping[str, object]) -> dict[str, object] | None:
    """Reject a stored package that is missing, foreign, inactive, or not a PT sale."""

    raw = payload.get("service_package_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    package_id = _uuid_value(raw)
    if package_id is None:
        return {"error": "package_unavailable"}
    package = _package_by_id(gym, package_id)
    if package is None or not _sale_eligible(package, _sale_currency(payload, gym)):
        return {"error": "package_unavailable"}
    return None


def sale_package_choice(gym: Gym, conversation: Conversation, *, currency: str) -> PackageChoice:
    """Identify a package from staff text and the previous question, never from a description."""

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = list(ServicePackage.objects.filter(gym=gym).order_by("code", "id"))
        staff = _staff_bodies(gym, conversation)
        assistant = (
            Message.objects.filter(
                gym=gym,
                conversation=conversation,
                role=Message.Role.ASSISTANT,
            )
            .order_by("-created_at", "-id")
            .only("body")
            .first()
        )
    latest = staff[0] if staff else ""
    question = assistant.body if assistant is not None else ""
    older = "\n".join(staff[1:])
    for kind, text in (("staff", latest), ("assistant", question), ("older", older)):
        if not text.strip():
            continue
        if (
            kind == "staff"
            and _talks_about_package(text)
            and not _match_packages(rows, text)
            and _unmatched_package_code(text, rows)
        ):
            return PackageChoice("unavailable")
        choice = _choice_from_text(rows, text, currency)
        if choice.status != "unnamed":
            return choice
    return PackageChoice("unnamed")


def _choice_from_text(
    rows: Sequence[ServicePackage],
    text: str,
    currency: str,
) -> PackageChoice:
    hits = _match_packages(rows, text)
    if not hits:
        return PackageChoice("unnamed")
    if len(hits) > 1:
        return PackageChoice("ambiguous", matches=tuple(hits))
    row = hits[0]
    if not _sale_eligible(row, currency):
        if row.currency != currency:
            return PackageChoice("currency_mismatch")
        return PackageChoice("unavailable")
    return PackageChoice("one", package=row)


def _match_packages(rows: Sequence[ServicePackage], text: str) -> list[ServicePackage]:
    records = tuple(
        NamedRecord(str(row.id), row.name, label=row.name, code=row.code or "") for row in rows
    )
    coded = _by_code(records, text)
    chosen = coded if coded else _mentioned(records, text)
    ids = {record.id for record in chosen}
    return [row for row in rows if str(row.id) in ids]


def _sale_eligible(package: ServicePackage, currency: str) -> bool:
    return (
        package.type == ServicePackage.PackageType.PT
        and package.is_active
        and package.currency == currency
    )


def _sale_currency(payload: Mapping[str, object], gym: Gym) -> str:
    currency = payload.get("currency")
    if isinstance(currency, str) and currency.strip():
        return currency.strip()
    return str(gym.currency)


def _talks_about_package(text: str) -> bool:
    return "ပက်ကေ့" in text or "package" in text.casefold()


def _unmatched_package_code(text: str, rows: Sequence[ServicePackage]) -> bool:
    codes = {_fold(row.code) for row in rows if row.code}
    return any(match.group(0) not in codes for match in _PACKAGE_CODE.finditer(_fold(text)))


def _package_by_id(gym: Gym, package_id: UUID) -> ServicePackage | None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return ServicePackage.objects.filter(gym=gym, id=package_id).first()


def _eligible_stored_package(
    gym: Gym,
    state: Mapping[str, str],
    currency: str,
) -> ServicePackage | None:
    package_id = _uuid_value(state.get("package_id"))
    if package_id is None:
        return None
    package = _package_by_id(gym, package_id)
    if package is None or not _sale_eligible(package, currency):
        return None
    return package


def _conversation_names_package(gym: Gym, conversation: Conversation) -> bool:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        staff = _staff_bodies(gym, conversation)
        assistant = (
            Message.objects.filter(
                gym=gym,
                conversation=conversation,
                role=Message.Role.ASSISTANT,
            )
            .order_by("-created_at", "-id")
            .only("body")
            .first()
        )
    question = assistant.body if assistant is not None else ""
    return any(_talks_about_package(text) for text in (*staff, question))


def _description_points_at_package(gym: Gym, payload: Mapping[str, object]) -> bool:
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        return False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = list(ServicePackage.objects.filter(gym=gym))
    return bool(_match_packages(rows, description))


def _bind_package(
    payload: dict[str, object],
    state: Mapping[str, str],
    package: ServicePackage,
) -> dict[str, str]:
    payload["service_package_id"] = str(package.id)
    _remember_package(state, package)
    return _package_label(package)


def _remember_package(state: Mapping[str, str], package: ServicePackage) -> None:
    if isinstance(state, dict):
        state["package_id"] = str(package.id)
        state["package_code"] = package.code
        state["package_name"] = package.name
        if not state.get("workflow"):
            state["workflow"] = "pt"


def _package_label(package: ServicePackage) -> dict[str, str]:
    return {"service_package_id": f"{package.name} ({package.code})"}


def _package_ambiguous(packages: Sequence[ServicePackage]) -> dict[str, object]:
    return {
        "error": "ambiguous",
        "field": "service_package_id",
        "matches": [
            {"name": f"{package.name} ({package.code})"} for package in packages if package
        ],
    }


def _occurrences(gym: Gym) -> tuple[NamedRecord, ...]:
    rows = (
        ClassOccurrence.objects.filter(
            gym=gym,
            status=ClassOccurrence.Status.SCHEDULED,
        )
        .select_related("class_type")
        .order_by("local_date", "starts_at")
    )
    return tuple(
        NamedRecord(
            str(row.id),
            row.class_type.name,
            when=row.local_date.isoformat(),
            label=f"{row.class_type.name} {row.local_date.isoformat()}",
        )
        for row in rows
    )


_LOADERS: dict[str, Callable[[Gym], tuple[NamedRecord, ...]]] = {
    "member_id": _members,
    "trainer_staff_profile_id": _trainers,
    "class_type_id": _class_types,
    "class_occurrence_id": _occurrences,
    "product_id": _products,
    "plan_id": _plans,
    "service_package_id": _service_packages,
}
_CATALOG_PERMISSIONS: dict[str, frozenset[str]] = {
    "product_id": frozenset({"inventory.view", "inventory.manage", "inventory.adjust"}),
    "plan_id": frozenset({"memberships.view", "memberships.create", "memberships.update"}),
    "service_package_id": frozenset(
        {
            "pt.schedule.view",
            "pt.schedule.manage",
            "pt.sales.create",
            "pt.sessions.adjust",
        }
    ),
}


def _denied_catalog(
    present: Mapping[str, str],
    line_products: Sequence[tuple[int, str]],
    permission_codes: frozenset[str],
) -> str | None:
    fields = [field for field in present if field in _CATALOG_PERMISSIONS]
    if line_products and "product_id" not in fields:
        fields.append("product_id")
    for field in fields:
        if _CATALOG_PERMISSIONS[field].isdisjoint(permission_codes):
            return field
    return None


def _line_product_values(payload: Mapping[str, object]) -> list[tuple[int, str]]:
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return []
    values: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            continue
        product_id = line.get("product_id")
        if isinstance(product_id, str) and product_id.strip():
            values.append((index, product_id))
    return values


def _choice_failure(field: str, choice: Choice) -> dict[str, object] | None:
    if choice.error == "not_found":
        return {"error": "not_found", "field": field}
    if choice.error == "ambiguous":
        return {
            "error": "ambiguous",
            "field": field,
            "matches": [
                {"id": record.id, "name": record.shown(), "date": record.when}
                for record in choice.matches
            ],
        }
    return None


def _resolve_line_products(
    payload: Mapping[str, object],
    products: Sequence[NamedRecord],
    staff_text: str,
) -> tuple[list[object], dict[str, str], dict[str, object] | None]:
    lines = payload.get("lines")
    copied: list[object] = []
    labels: dict[str, str] = {}
    if not isinstance(lines, list):
        return copied, labels, None
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            copied.append(line)
            continue
        item = dict(line)
        product_id = item.get("product_id")
        if isinstance(product_id, str) and product_id.strip():
            field = f"lines.{index}.product_id"
            choice = choose_reference(product_id, products, staff_text)
            failure = _choice_failure(field, choice)
            if failure is not None:
                return [], {}, failure
            item["product_id"] = choice.id
            labels[field] = choice.label
        copied.append(item)
    return copied, labels, None


def _ambiguous_named(records: Sequence[NamedRecord]) -> bool:
    counts: dict[str, int] = {}
    for record in records:
        if record.when:
            continue
        key = _fold(record.name)
        counts[key] = counts.get(key, 0) + 1
    return any(count > 1 for count in counts.values())


def _by_code(records: Sequence[NamedRecord], text: str) -> list[NamedRecord]:
    folded = _fold(text)
    if not folded:
        return []
    return [
        record for record in records if record.code and _code_present(_fold(record.code), folded)
    ]


def _code_present(code: str, folded: str) -> bool:
    """Match a whole code. YOGA must not match inside YOGA_2."""

    if not code:
        return False
    return re.search(rf"(?<![a-z0-9_]){re.escape(code)}(?![a-z0-9_])", folded) is not None


def _mentioned(records: Sequence[NamedRecord], text: str) -> list[NamedRecord]:
    folded = _fold(text)
    hits = [record for record in records if _fold(record.name) and _fold(record.name) in folded]
    return _drop_shorter(hits, folded)


def _name_hits(records: Sequence[NamedRecord], supplied: str) -> list[NamedRecord]:
    folded = _fold(supplied)
    if not folded:
        return []
    hits = [
        record
        for record in records
        if folded == _fold(record.name)
        or folded in _fold(record.name)
        or _fold(record.name) in folded
    ]
    return _drop_shorter(hits, folded)


def _drop_shorter(records: Sequence[NamedRecord], folded_text: str) -> list[NamedRecord]:
    kept: list[NamedRecord] = []
    for record in records:
        name = _fold(record.name)
        if any(
            name != _fold(other.name)
            and name in _fold(other.name)
            and _fold(other.name) in folded_text
            for other in records
        ):
            continue
        kept.append(record)
    return kept


def _dated(records: Sequence[NamedRecord], dates: set[str]) -> list[NamedRecord]:
    if not dates or not any(record.when for record in records):
        return list(records)
    return [record for record in records if not record.when or record.when in dates]


def _needs_a_date(
    current: NamedRecord,
    records: Sequence[NamedRecord],
    staff_text: str,
    dates: set[str],
) -> bool:
    if dates or not current.when or not _mentioned((current,), staff_text):
        return False
    return len(_same_name(current, records)) > 1


def _same_name(current: NamedRecord, records: Sequence[NamedRecord]) -> list[NamedRecord]:
    folded = _fold(current.name)
    return [record for record in records if record.when and _fold(record.name) == folded]


def _uuid_text(value: object) -> str | None:
    parsed = _uuid_value(value)
    return None if parsed is None else str(parsed)


def _uuid_value(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


_GATED_TOOLS = frozenset(
    {
        "create_membership",
        "create_payment",
        "update_payment",
        "refund_payment",
        "void_payment",
        "create_class_booking",
        "create_pt_booking",
        "create_retail_sale",
    }
)
_OVERLAP_STATUSES = (
    Membership.Status.PENDING,
    Membership.Status.ACTIVE,
    Membership.Status.SUSPENDED,
)
_UNMETERED_PHRASES = (
    "unmetered",
    "complimentary",
    "without a package",
    "no package",
    "free session",
    "အခမဲ့",
    "ပက်ကေ့မလို",
    "ပက်ကေ့ မလို",
)
_NUMBER_TOKEN = re.compile(r"\d+(?:[,၊]\d{3})*(?:\.\d+)?")


def unmetered_gate(
    gym: Gym,
    actor: GymMembership | None,
    tool_name: str,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    """Keep an unmetered flag only when the staff asked for it."""

    if tool_name not in _UNMETERED_TOOLS:
        return None
    if not _staff_requested_unmetered(staff_text):
        payload.pop("unmetered", None)
        payload.pop("unmetered_reason", None)
        return None
    reason = payload.get("unmetered_reason")
    text = reason.strip() if isinstance(reason, str) else ""
    if text == "":
        text = " ".join(staff_text.split())[:500]
    if text == "":
        return {"error": "unmetered_reason"}
    if actor is None or not _may_book_unmetered(gym, actor):
        return {"error": "unmetered_denied"}
    payload["unmetered"] = True
    payload["unmetered_reason"] = text
    return None


def proposal_block(
    gym: Gym,
    actor: GymMembership | None,
    tool_name: str,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    """Stop a proposal the current gym rows already show is invalid."""

    if tool_name not in _GATED_TOOLS:
        return None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if tool_name == "create_membership":
            return _membership_sale_gate(gym, actor, payload, staff_text)
        if tool_name == "create_class_booking":
            return _class_booking_gate(gym, actor, payload, staff_text)
        if tool_name == "create_pt_booking":
            return _pt_booking_gate(gym, payload)
        if tool_name == "create_retail_sale":
            return _retail_gate(gym, payload, staff_text)
        if tool_name == "create_payment":
            return _payment_create_gate(gym, payload, staff_text)
        if tool_name == "update_payment":
            return _payment_update_gate(gym, payload, staff_text)
        if tool_name == "refund_payment":
            return _refund_gate(gym, payload, staff_text)
        if tool_name == "void_payment":
            return _void_gate(gym, payload, staff_text)
    return None


def membership_conflict(gym: Gym, payload: Mapping[str, object]) -> dict[str, str]:
    """Return the membership that blocks the stored start date."""

    member_id = _uuid_text(payload.get("member_id"))
    plan_id = _uuid_text(payload.get("plan_id"))
    start = _service_day(payload.get("start_date"), gym)
    if member_id is None or plan_id is None or start is None:
        return {}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        plan = MembershipPlan.objects.filter(gym=gym, id=plan_id).first()
        if plan is None:
            return {}
        expiry = inclusive_expiry_date(start, int(plan.duration_value), str(plan.duration_unit))
        conflict = (
            Membership.objects.filter(
                gym=gym,
                member_id=member_id,
                status__in=_OVERLAP_STATUSES,
                start_date__lte=expiry,
                expiry_date__gte=start,
            )
            .order_by("start_date", "id")
            .first()
        )
    if conflict is None:
        return {}
    return {
        "plan": conflict.plan_name,
        "start": conflict.start_date.isoformat(),
        "end": conflict.expiry_date.isoformat(),
    }


def member_has_class_booking(gym: Gym, payload: Mapping[str, object]) -> bool:
    """Return whether this member already has an active booking for the class."""

    member = _uuid_text(payload.get("member_id"))
    occurrence = _uuid_text(payload.get("class_occurrence_id"))
    if member is None or occurrence is None:
        return False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return _active_class_booking(gym, member, occurrence)


def refundable_balance(gym: Gym, payment_id: str) -> Decimal | None:
    """Return the posted charge's remaining refundable amount."""

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = _gym_payment(gym, payment_id)
        if payment is None:
            return None
        return _refundable(payment)


def _membership_sale_gate(
    gym: Gym,
    actor: GymMembership | None,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    member_id = _uuid_text(payload.get("member_id"))
    plan_id = _uuid_text(payload.get("plan_id"))
    start = _service_day(payload.get("start_date"), gym)
    if member_id is None:
        return {"error": "missing_fields", "fields": ["member_id"]}
    if plan_id is None:
        return {"error": "missing_fields", "fields": ["plan_id"]}
    if start is None:
        return {"error": "missing_fields", "fields": ["start_date"]}
    member = Member.objects.filter(gym=gym, id=member_id).first()
    if member is None:
        return {"error": "not_found", "field": "member_id"}
    if member.status != Member.Status.ACTIVE:
        return {"error": "member_inactive"}
    if actor is not None and not _member_visible(gym, actor, member.id, start):
        return {"error": "not_found", "field": "member_id"}
    plan = MembershipPlan.objects.filter(gym=gym, id=plan_id).first()
    if plan is None:
        return {"error": "not_found", "field": "plan_id"}
    if not plan.is_active:
        return {"error": "plan_inactive"}
    discount = _stated_discount(payload, staff_text)
    if isinstance(discount, dict):
        return discount
    expiry = inclusive_expiry_date(start, int(plan.duration_value), str(plan.duration_unit))
    conflict = (
        Membership.objects.filter(
            gym=gym,
            member_id=member.id,
            status__in=_OVERLAP_STATUSES,
            start_date__lte=expiry,
            expiry_date__gte=start,
        )
        .order_by("start_date", "id")
        .first()
    )
    if conflict is not None:
        return {
            "error": "membership_overlap",
            "plan": conflict.plan_name,
            "start": conflict.start_date.isoformat(),
            "end": conflict.expiry_date.isoformat(),
        }
    return None


def _class_booking_gate(
    gym: Gym,
    actor: GymMembership | None,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    member_id = _uuid_text(payload.get("member_id"))
    occurrence_id = _uuid_text(payload.get("class_occurrence_id"))
    if member_id is None or occurrence_id is None:
        return {"error": "missing_fields", "fields": ["class_occurrence_id"]}
    member = Member.objects.filter(gym=gym, id=member_id).first()
    if member is None or member.status != Member.Status.ACTIVE:
        return {"error": "member_inactive" if member is not None else "not_found"}
    occurrence = ClassOccurrence.objects.filter(gym=gym, id=occurrence_id).first()
    if occurrence is None or occurrence.status != ClassOccurrence.Status.SCHEDULED:
        return {"error": "not_found", "field": "class_occurrence_id"}
    if actor is not None and not _member_visible(gym, actor, member.id, occurrence.local_date):
        return {"error": "not_found", "field": "member_id"}
    taken = (
        ClassBooking.objects.filter(gym=gym, class_occurrence_id=occurrence.id)
        .exclude(status=ClassBooking.Status.CANCELLED)
        .count()
    )
    if taken >= occurrence.capacity:
        return {"error": "class_full"}
    if _active_class_booking(gym, member_id, occurrence_id):
        return {"error": "duplicate_booking"}
    if _class_time_conflict(gym, member_id, occurrence):
        return {"error": "schedule_conflict"}
    if _is_unmetered(payload.get("unmetered")):
        payload.pop("member_service_package_id", None)
        return None
    return _bind_class_package(gym, payload, member_id, occurrence, staff_text)


def _pt_booking_gate(gym: Gym, payload: dict[str, object]) -> dict[str, object] | None:
    start = _parsed_moment(payload.get("starts_at"), gym)
    end = _parsed_moment(payload.get("ends_at"), gym)
    if start is None or end is None:
        return {"error": "time_unconfirmed", "field": "starts_at"}
    if end <= start:
        return {"error": "time_order"}
    trainer = _uuid_value(payload.get("trainer_staff_profile_id"))
    member = _uuid_text(payload.get("member_id"))
    if trainer is None or member is None:
        return None
    if _range_overlap(
        PTBooking.objects.filter(gym=gym, trainer_staff_profile_id=trainer),
        start,
        end,
    ):
        return {"error": "trainer_unavailable"}
    if _range_overlap(PTBooking.objects.filter(gym=gym, member_id=member), start, end):
        return {"error": "schedule_conflict"}
    if _member_class_overlap(gym, member, start, end):
        return {"error": "schedule_conflict"}
    return None


def _retail_gate(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    day = _service_day(payload.get("business_date"), gym)
    if day is not None and day > _local_today(gym):
        return {"error": "date_invalid"}
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return {"error": "missing_fields", "fields": ["lines"]}
    quantities = _stated_quantities(staff_text)
    requested: dict[str, int] = {}
    copied: list[object] = []
    for line in lines:
        if not isinstance(line, dict):
            return {"error": "quantity_invalid"}
        item = dict(line)
        product_id = _uuid_text(item.get("product_id"))
        if product_id is None:
            return {"error": "not_found", "field": "product_id"}
        quantity_error = _quantity_error(item.get("quantity"), quantities)
        if quantity_error is not None:
            return quantity_error
        quantity = _whole_quantity(item.get("quantity"))
        if quantity is None:
            return {"error": "quantity_invalid"}
        product = Product.objects.filter(gym=gym, id=product_id).first()
        if product is None:
            return {"error": "not_found", "field": "product_id"}
        if not product.is_active:
            return {"error": "product_inactive", "name": product.name}
        discount = _line_discount(item, staff_text)
        if isinstance(discount, dict):
            return discount
        item["quantity"] = str(quantity)
        item["discount"] = discount
        item.pop("unit_price", None)
        item.pop("sale_price", None)
        item.pop("unit_cost", None)
        copied.append(item)
        requested[product_id] = requested.get(product_id, 0) + quantity
    if len(requested) != len(copied):
        return {"error": "duplicate_product"}
    for product_id, quantity in requested.items():
        if _on_hand(gym, product_id) < Decimal(quantity):
            return {"error": "stock_short", "quantity": str(quantity)}
    payload["lines"] = copied
    return None


def _payment_create_gate(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    _canonicalize_payment(payload, gym)
    if str(payload.get("currency", "")).upper() != gym.currency:
        return {"error": "currency_mismatch"}
    purpose = str(payload.get("purpose", "")).strip().upper()
    amount_error = _require_stated_amount(payload, staff_text, required=True)
    if amount_error is not None:
        return amount_error
    day = _service_day(payload.get("business_date"), gym)
    if day is not None and day > _local_today(gym):
        return {"error": "date_invalid"}
    if purpose != Payment.Purpose.MEMBERSHIP:
        payload.pop("membership_id", None)
        return None
    return _bind_membership_payment(gym, payload, staff_text)


def _payment_update_gate(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    payment, failure = _resolve_payment(
        gym,
        payload,
        staff_text,
        statuses=(Payment.Status.DRAFT,),
        entry_type=Payment.EntryType.CHARGE,
    )
    if failure is not None:
        return failure
    assert payment is not None
    if payment.pt_sale_id is not None:
        return {"error": "payment_state"}
    if payment.status != Payment.Status.DRAFT:
        return {"error": "payment_state"}
    payload["payment_id"] = str(payment.id)
    _canonicalize_payment(payload, gym)
    if "amount" in payload:
        amount_error = _require_stated_amount(payload, staff_text, required=True)
        if amount_error is not None:
            return amount_error
    purpose = str(payload.get("purpose", payment.purpose)).strip().upper()
    if purpose == Payment.Purpose.MEMBERSHIP or (
        "membership_id" in payload and purpose != Payment.Purpose.OTHER
    ):
        if "member_id" not in payload and payment.member_id is not None:
            payload["member_id"] = str(payment.member_id)
        if purpose == Payment.Purpose.MEMBERSHIP:
            payload["purpose"] = Payment.Purpose.MEMBERSHIP
            bound = _bind_membership_payment(gym, payload, staff_text)
            if bound is not None:
                return bound
    elif purpose == Payment.Purpose.OTHER:
        payload.pop("membership_id", None)
    day = _service_day(payload.get("business_date"), gym)
    if day is not None and day > _local_today(gym):
        return {"error": "date_invalid"}
    return None


def _refund_gate(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    payment, failure = _resolve_payment(
        gym,
        payload,
        staff_text,
        statuses=(Payment.Status.POSTED,),
        entry_type=Payment.EntryType.CHARGE,
    )
    if failure is not None:
        return failure
    assert payment is not None
    state = _charge_state(payment)
    if state is not None:
        return state
    payload["payment_id"] = str(payment.id)
    amount_error = _require_stated_amount(payload, staff_text, required=True)
    if amount_error is not None:
        return amount_error
    amount = _decimal_amount(payload.get("amount"))
    balance = _refundable(payment)
    if amount is None or amount > balance:
        return {"error": "refund_exceeds", "balance": format(balance, "f")}
    day = _service_day(payload.get("business_date"), gym)
    if day is not None and (day > _local_today(gym) or day < payment.business_date):
        return {"error": "date_invalid"}
    return None


def _void_gate(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    payment, failure = _resolve_payment(
        gym,
        payload,
        staff_text,
        statuses=(Payment.Status.POSTED,),
        entry_type=Payment.EntryType.CHARGE,
    )
    if failure is not None:
        return failure
    assert payment is not None
    state = _charge_state(payment)
    if state is not None:
        return state
    balance = _refundable(payment)
    if balance != Decimal(payment.amount).quantize(_MONEY):
        return {"error": "void_blocked", "balance": format(balance, "f")}
    payload["payment_id"] = str(payment.id)
    day = _service_day(payload.get("business_date"), gym)
    if day is not None and (day > _local_today(gym) or day < payment.business_date):
        return {"error": "date_invalid"}
    return None


def _bind_membership_payment(
    gym: Gym,
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | None:
    member_id = _uuid_text(payload.get("member_id"))
    if member_id is None:
        return {"error": "missing_fields", "fields": ["member_id"]}
    rows = list(
        Membership.objects.filter(gym=gym, member_id=member_id)
        .select_related("plan")
        .order_by("start_date", "id")
    )
    supplied = _uuid_text(payload.get("membership_id"))
    owned = {str(row.id) for row in rows}
    if supplied is not None and supplied not in owned:
        return {"error": "membership_mismatch"}
    named = [
        row
        for row in rows
        if (row.plan_code and _fold(row.plan_code) in _fold(staff_text))
        or (row.plan_name and _fold(row.plan_name) in _fold(staff_text))
    ]
    if len(named) == 1:
        chosen = named[0]
    elif len(rows) == 1:
        chosen = rows[0]
    else:
        return {"error": "membership_required"}
    if str(payload.get("currency", gym.currency)).upper() != gym.currency:
        return {"error": "currency_mismatch"}
    if chosen.currency != gym.currency:
        return {"error": "currency_mismatch"}
    payload["membership_id"] = str(chosen.id)
    payload["currency"] = gym.currency
    amount = _decimal_amount(payload.get("amount"))
    if amount is None:
        return {"error": "amount_unconfirmed"}
    outstanding = _outstanding(chosen)
    if amount > outstanding:
        return {"error": "balance_exceeded", "balance": format(outstanding, "f")}
    return None


def _resolve_payment(
    gym: Gym,
    payload: Mapping[str, object],
    staff_text: str,
    *,
    statuses: tuple[str, ...],
    entry_type: str,
) -> tuple[Payment | None, dict[str, object] | None]:
    supplied = _uuid_text(payload.get("payment_id"))
    supplied_row = _gym_payment(gym, supplied) if supplied is not None else None
    if supplied is not None and supplied_row is None:
        return None, {"error": "not_found", "field": "payment_id"}
    receipt_hits = _payments_named_by_receipt(gym, staff_text)
    if len(receipt_hits) > 1:
        return None, {"error": "payment_unconfirmed"}
    if len(receipt_hits) == 1:
        return receipt_hits[0], None
    rows = list(
        Payment.objects.filter(gym=gym, status__in=statuses, entry_type=entry_type).order_by(
            "-received_at", "id"
        )
    )
    member_id = _uuid_text(payload.get("member_id"))
    amounts = _payment_amounts(_stated_decimals(staff_text))
    if len(amounts) == 1:
        wanted = amounts[0]
        rows = [row for row in rows if Decimal(row.amount).quantize(_MONEY) == wanted]
    matched_member = False
    if member_id is not None:
        named = [row for row in rows if str(row.member_id) == member_id]
        if named:
            rows = named
            matched_member = True
    if len(rows) == 1 and (matched_member or len(amounts) == 1):
        return rows[0], None
    if supplied_row is not None and (
        supplied_row.status not in statuses or supplied_row.entry_type != entry_type
    ):
        return None, {"error": "payment_state"}
    return None, {"error": "payment_unconfirmed"}


def _charge_state(payment: Payment) -> dict[str, object] | None:
    if payment.entry_type != Payment.EntryType.CHARGE or payment.status != Payment.Status.POSTED:
        return {"error": "payment_state"}
    if payment.purpose == Payment.Purpose.PRODUCT or payment.retail_sale_id is not None:
        return {"error": "retail_charge"}
    if payment.pt_sale_id is not None:
        return {"error": "payment_state"}
    return None


def _payments_named_by_receipt(gym: Gym, staff_text: str) -> list[Payment]:
    folded = _fold(staff_text)
    if not folded:
        return []
    hits: list[Payment] = []
    receipts = Receipt.objects.filter(gym=gym).exclude(receipt_number="")
    for receipt in receipts:
        number = _fold(receipt.receipt_number)
        if len(number) >= 4 and number in folded:
            payment = _gym_payment(gym, str(receipt.payment_id))
            if payment is not None:
                hits.append(payment)
    return hits


def _bind_class_package(
    gym: Gym,
    payload: dict[str, object],
    member_id: str,
    occurrence: ClassOccurrence,
    staff_text: str,
) -> dict[str, object] | None:
    rows = (
        MemberServicePackage.objects.filter(
            gym=gym,
            member_id=member_id,
            package_type="CLASS",
            status=MemberServicePackage.Status.ACTIVE,
            valid_from__lte=occurrence.local_date,
            valid_until__gte=occurrence.local_date,
            service_package__type="CLASS",
            service_package__class_type_id=occurrence.class_type_id,
        )
        .select_related("service_package")
        .annotate(remaining=Sum("ledger_entries__session_delta"))
    )
    eligible = [row for row in rows if (row.remaining or 0) > 0]
    if len(eligible) == 1:
        payload["member_service_package_id"] = str(eligible[0].id)
        return None
    folded = _fold(staff_text)
    named = []
    for row in eligible:
        definition = row.service_package
        if definition is None:
            continue
        code = _fold(definition.code)
        name = _fold(definition.name)
        if (code and code in folded) or (name and name in folded):
            named.append(row)
    if len(named) == 1:
        payload["member_service_package_id"] = str(named[0].id)
        return None
    return {"error": "class_package_required", "member_id": member_id}


def _outstanding(membership: Membership) -> Decimal:
    from apps.members.payment_services import _posted_total

    paid = _posted_total(membership)
    return (Decimal(membership.final_amount) - paid).quantize(_MONEY)


def _refundable(payment: Payment) -> Decimal:
    related = Payment.objects.filter(gym_id=payment.gym_id, original_payment_id=payment.id)
    used = sum(
        (
            Decimal(row.amount)
            for row in related
            if row.status == Payment.Status.POSTED
            and row.entry_type in {Payment.EntryType.REFUND, Payment.EntryType.REVERSAL}
        ),
        Decimal("0.00"),
    )
    return (Decimal(payment.amount) - used).quantize(_MONEY)


def _on_hand(gym: Gym, product_id: str) -> Decimal:
    total = StockMovement.objects.filter(gym=gym, product_id=product_id).aggregate(
        total=Sum("quantity")
    )["total"]
    if total is None:
        return Decimal("0.000")
    return Decimal(total)


def _active_class_booking(gym: Gym, member_id: str, occurrence_id: str) -> bool:
    return (
        ClassBooking.objects.filter(
            gym=gym,
            member_id=member_id,
            class_occurrence_id=UUID(occurrence_id),
        )
        .exclude(status=ClassBooking.Status.CANCELLED)
        .exists()
    )


def _class_time_conflict(gym: Gym, member_id: str, occurrence: ClassOccurrence) -> bool:
    others = (
        ClassBooking.objects.filter(gym=gym, member_id=member_id)
        .exclude(status=ClassBooking.Status.CANCELLED)
        .exclude(class_occurrence_id=occurrence.id)
        .select_related("class_occurrence")
    )
    for booking in others:
        other = booking.class_occurrence
        if other.starts_at < occurrence.ends_at and other.ends_at > occurrence.starts_at:
            return True
    return (
        PTBooking.objects.filter(gym=gym, member_id=member_id)
        .exclude(status=PTBooking.Status.CANCELLED)
        .filter(starts_at__lt=occurrence.ends_at, ends_at__gt=occurrence.starts_at)
        .exists()
    )


def _member_class_overlap(gym: Gym, member_id: str, start: datetime, end: datetime) -> bool:
    bookings = (
        ClassBooking.objects.filter(gym=gym, member_id=member_id)
        .exclude(status=ClassBooking.Status.CANCELLED)
        .select_related("class_occurrence")
    )
    for booking in bookings:
        other = booking.class_occurrence
        if other.starts_at < end and other.ends_at > start:
            return True
    return False


def _range_overlap(rows: QuerySet[PTBooking], start: datetime, end: datetime) -> bool:
    return (
        rows.exclude(status=PTBooking.Status.CANCELLED)
        .filter(
            starts_at__lt=end,
            ends_at__gt=start,
        )
        .exists()
    )


def _gym_payment(gym: Gym, payment_id: str) -> Payment | None:
    return Payment.objects.filter(gym=gym, id=payment_id).first()


def _member_visible(gym: Gym, actor: GymMembership, member_id: UUID, day: date) -> bool:
    from apps.trainers.selectors import require_assigned_member

    try:
        require_assigned_member(
            gym_id=gym.id,
            actor=actor,
            member_id=member_id,
            as_of=day,
        )
    except Http404:
        return False
    return True


def _may_book_unmetered(gym: Gym, actor: GymMembership) -> bool:
    from apps.trainers.package_services import _assert_adjuster

    try:
        _assert_adjuster(gym=gym, actor=actor)
    except PermissionDenied:
        return False
    return True


def _staff_requested_unmetered(text: str) -> bool:
    folded = text.casefold()
    return any(phrase in text or phrase in folded for phrase in _UNMETERED_PHRASES)


def _canonicalize_payment(payload: dict[str, object], gym: Gym) -> None:
    purpose = payload.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        payload["purpose"] = purpose.strip().upper()
    method = payload.get("method")
    if isinstance(method, str) and method.strip():
        payload["method"] = method.strip().upper()
    currency = payload.get("currency")
    if not isinstance(currency, str) or currency.strip() == "":
        payload["currency"] = gym.currency
    elif currency.strip().upper() == gym.currency:
        payload["currency"] = gym.currency


def _require_stated_amount(
    payload: Mapping[str, object],
    staff_text: str,
    *,
    required: bool,
) -> dict[str, object] | None:
    if "amount" not in payload and not required:
        return None
    amount = _decimal_amount(payload.get("amount"))
    stated = _payment_amounts(_stated_decimals(staff_text))
    if amount is None or not stated:
        return {"error": "amount_unconfirmed"}
    if amount not in stated:
        return {"error": "amount_mismatch"}
    return None


def _stated_discount(
    payload: dict[str, object],
    staff_text: str,
) -> dict[str, object] | str | None:
    if "discount_amount" not in payload:
        return None
    amount = _decimal_amount(payload.get("discount_amount"))
    if amount is None or amount == 0:
        return None
    if amount not in _stated_decimals(staff_text):
        return {"error": "amount_mismatch"}
    return None


def _line_discount(line: dict[str, object], staff_text: str) -> dict[str, object] | str:
    amount = _decimal_amount(line.get("discount"))
    if amount is None or amount == 0:
        return "0"
    if amount not in _stated_decimals(staff_text):
        return {"error": "amount_mismatch"}
    return format(amount, "f")


def _stated_decimals(text: str) -> tuple[Decimal, ...]:
    converted = text.translate(_MYANMAR_DIGITS)
    working = _without_calendar_text(converted)
    working = re.sub(r"\d{1,2}:\d{2}", " ", working)
    working = re.sub(r"\d{1,2}\s*(?:am|pm)\b", " ", working, flags=re.IGNORECASE)
    working = re.sub(r"\d{1,2}\s*နာရီ(?:\s*\d{1,2}\s*မိနစ်)?", " ", working)
    found: list[Decimal] = []
    for match in _NUMBER_TOKEN.finditer(working):
        if match.start() > 0 and working[match.start() - 1] in "-_/":
            continue
        token = match.group(0).replace(",", "").replace("၊", "")
        try:
            number = Decimal(token)
        except ArithmeticError:
            continue
        if number not in found:
            found.append(number)
    return tuple(found)


def _payment_amounts(numbers: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    quantized = tuple(number.quantize(_MONEY) for number in numbers)
    money = tuple(
        number for number in quantized if number >= 100 or number != number.to_integral_value()
    )
    return money or quantized


def _stated_quantities(text: str) -> tuple[int, ...]:
    wholes = tuple(
        int(number)
        for number in _stated_decimals(text)
        if number == number.to_integral_value() and number > 0
    )
    small = tuple(number for number in wholes if number < 100)
    return small or wholes


def _quantity_error(
    value: object,
    quantities: tuple[int, ...],
) -> dict[str, object] | None:
    if value is None or value == "":
        return {"error": "quantity_unconfirmed"}
    quantity = _whole_quantity(value)
    if quantity is None:
        return {"error": "quantity_invalid"}
    if quantity not in quantities:
        return {"error": "quantity_invalid" if quantities else "quantity_unconfirmed"}
    return None


def _whole_quantity(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        return number if number > 0 else None
    return None


def _decimal_amount(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except ArithmeticError:
        return None
    if not amount.is_finite():
        return None
    return amount.quantize(_MONEY)


def _parsed_moment(value: object, gym: Gym) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    zone = _gym_zone(gym)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)

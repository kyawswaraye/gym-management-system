"""Class occurrence and booking reads stay bounded and do not invent values."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.assistant.class_reads import (
    class_read_tool_contracts,
    search_bookings,
    search_class_occurrences,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.proposals import declaration
from apps.tenants.models import Gym, GymMembership

_OCCURRENCE_KEYS = {
    "id",
    "class_type_id",
    "class_name",
    "local_date",
    "start_time",
    "end_time",
    "capacity",
    "booked_count",
    "status",
}
_BOOKING_KEYS = {
    "id",
    "class_occurrence_id",
    "member_id",
    "member_number",
    "member_name",
    "class_name",
    "local_date",
    "start_time",
    "end_time",
    "status",
    "created_at",
    "attended_at",
    "no_show_at",
    "cancelled_at",
}
_HIDDEN = {
    "phone",
    "email",
    "notes",
    "address",
    "unmetered_reason",
    "created_by_membership_id",
    "reason",
    "location",
}


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def filter(self, **kwargs: object) -> _Rows:
        after = kwargs.get("id__gt")
        if isinstance(after, UUID):
            self.rows = [row for row in self.rows if isinstance(row.id, UUID) and row.id > after]
        return self

    def __getitem__(self, item: object) -> list[SimpleNamespace]:
        if not isinstance(item, slice):
            raise AssertionError(item)
        return self.rows[item]


def _gym() -> Gym:
    return Gym(
        id=uuid4(),
        code="class",
        name="Class",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
    )


def _actor(gym: Gym) -> GymMembership:
    return GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE)


def _call(gym: Gym, payload: dict[str, object], actor: GymMembership | None = None) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=actor or _actor(gym),
        payload=payload,
        request_id="req_class_read",
        ip_address=None,
        user_agent="",
    )


def _occurrence() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        class_type_id=uuid4(),
        class_type=SimpleNamespace(name="Yoga"),
        trainer_staff_profile_id=uuid4(),
        local_date=date(2026, 11, 12),
        starts_at=datetime(2026, 11, 11, 17, 45, tzinfo=UTC),
        ends_at=datetime(2026, 11, 11, 18, 30, tzinfo=UTC),
        capacity=8,
        booked_count=2,
        status="SCHEDULED",
        location="secret room",
        reason="secret-reason",
    )


def _member(member_id: UUID, first_name: str, number: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=member_id,
        member_number=number,
        first_name=first_name,
        last_name="Aung",
        phone="+959000000001",
        email="secret@example.test",
        notes="secret-note",
    )


def _booking(
    *,
    member: SimpleNamespace,
    occurrence: SimpleNamespace,
    status: str = "BOOKED",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        class_occurrence_id=occurrence.id,
        class_occurrence=occurrence,
        member_id=member.id,
        member=member,
        status=status,
        created_at=datetime(2026, 11, 11, 9, 0, tzinfo=UTC),
        attended_at=None,
        no_show_at=None,
        cancelled_at=None,
        unmetered_reason="secret-reason",
        created_by_membership_id=uuid4(),
        reason="secret-note",
    )


def test_class_reads_are_optional_and_do_not_advertise_class_type() -> None:
    occurrences = declaration(class_read_tool_contracts()["search_class_occurrences"])
    bookings = declaration(class_read_tool_contracts()["search_bookings"])
    occurrence_parameters = occurrences["parameters"]
    booking_parameters = bookings["parameters"]
    assert isinstance(occurrence_parameters, dict)
    assert isinstance(booking_parameters, dict)
    assert occurrence_parameters["required"] == []
    assert booking_parameters["required"] == []
    occurrence_properties = occurrence_parameters["properties"]
    booking_properties = booking_parameters["properties"]
    assert isinstance(occurrence_properties, dict)
    assert isinstance(booking_properties, dict)
    assert set(occurrence_properties) == {
        "occurrence_id",
        "date_from",
        "date_to",
        "status",
        "trainer_staff_profile_id",
        "limit",
        "after",
    }
    assert set(booking_properties) == {
        "member_id",
        "occurrence_id",
        "date_from",
        "date_to",
        "status",
        "limit",
        "after",
    }
    assert "class_type" not in occurrence_properties
    assert "class_type_id" not in occurrence_properties
    assert occurrence_properties["limit"]["type"] == "integer"
    occurrence_contract = class_read_tool_contracts()["search_class_occurrences"]
    booking_contract = class_read_tool_contracts()["search_bookings"]
    assert occurrence_contract.mode == ExecutionMode.READ
    assert booking_contract.mode == ExecutionMode.READ
    assert occurrence_contract.finance_closed is False
    assert booking_contract.finance_closed is False
    assert occurrence_contract.definition.permissions == ("classes.view",)
    assert booking_contract.definition.permissions == ("classes.view",)
    assert occurrence_contract.definition.features == ("pt",)
    assert booking_contract.definition.features == ("pt",)
    assert occurrence_contract.definition.confirmation_required is False
    assert booking_contract.definition.confirmation_required is False


def test_search_class_occurrences_uses_gym_local_time(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _occurrence()
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.class_reads.page_class_occurrences", page)
    monkeypatch.setattr(
        "apps.assistant.class_reads._detailed_occurrences",
        lambda _gym, rows: rows,
    )
    result = search_class_occurrences(
        _call(
            gym,
            {
                "date_from": "2026-11-12",
                "date_to": "2026-11-12",
                "status": "SCHEDULED",
                "limit": 1,
            },
        )
    )
    assert captured["gym_id"] == gym.id
    assert captured["date_from"] == date(2026, 11, 12)
    assert captured["date_to"] == date(2026, 11, 12)
    assert captured["status"] == "SCHEDULED"
    assert captured["limit"] == 1
    assert "class_type_id" not in captured
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["local_date"] == "2026-11-12"
    assert found[0]["start_time"] == "00:15"
    assert found[0]["end_time"] == "01:00"
    assert found[0]["capacity"] == 8
    assert found[0]["booked_count"] == 2
    assert found[0]["class_name"] == "Yoga"
    assert found[0]["status"] == "SCHEDULED"
    assert set(found[0]) == _OCCURRENCE_KEYS
    assert _HIDDEN.isdisjoint(found[0])


def test_occurrence_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_class_occurrences(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_class_occurrences(_call(gym, {"date_from": "2026-11-12", "date_to": "2026-11-11"}))
    with pytest.raises(ValidationError):
        search_class_occurrences(_call(gym, {"occurrence_id": "not-an-occurrence"}))
    with pytest.raises(ValidationError):
        search_class_occurrences(_call(gym, {"class_type_id": str(uuid4())}))
    with pytest.raises(ValidationError):
        search_class_occurrences(_call(gym, {"limit": 26}))


def test_missing_occurrence_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(**_kwargs: object) -> SimpleNamespace:
        raise Http404

    monkeypatch.setattr("apps.assistant.class_reads.get_visible_occurrence", missing)
    with pytest.raises(Http404):
        search_class_occurrences(_call(_gym(), {"occurrence_id": str(uuid4())}))


def test_occurrence_page_can_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.class_reads.page_class_occurrences",
        lambda **_kwargs: ([], {"has_more": False, "next_after": None}),
    )
    result = search_class_occurrences(
        _call(_gym(), {"date_from": "2020-01-01", "date_to": "2020-01-01"})
    )
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_booked_count_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _occurrence()
    del row.booked_count
    monkeypatch.setattr(
        "apps.assistant.class_reads.page_class_occurrences",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr(
        "apps.assistant.class_reads._detailed_occurrences",
        lambda _gym, rows: rows,
    )
    with pytest.raises(ExecutionRejected) as missing:
        search_class_occurrences(_call(_gym(), {}))
    assert missing.value.code == "read_result"


def test_naive_class_time_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _occurrence()
    row.starts_at = datetime(2026, 11, 12, 0, 15)
    monkeypatch.setattr(
        "apps.assistant.class_reads.page_class_occurrences",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr(
        "apps.assistant.class_reads._detailed_occurrences",
        lambda _gym, rows: rows,
    )
    with pytest.raises(ExecutionRejected) as missing:
        search_class_occurrences(_call(_gym(), {}))
    assert missing.value.code == "time"


def test_search_bookings_keeps_duplicate_names(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    occurrence = _occurrence()
    first = _booking(member=_member(uuid4(), "Aung", "MEM-000001"), occurrence=occurrence)
    second = _booking(member=_member(uuid4(), "Aung", "MEM-000002"), occurrence=occurrence)
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [first, second], {"has_more": True, "next_after": "cursor"}

    monkeypatch.setattr("apps.assistant.class_reads.page_class_bookings", page)
    monkeypatch.setattr("apps.assistant.class_reads._detailed_bookings", lambda _gym, rows: rows)
    result = search_bookings(
        _call(
            gym,
            {
                "member_id": str(first.member_id),
                "date_from": "2026-11-12",
                "date_to": "2026-11-12",
                "status": "BOOKED",
            },
        )
    )
    assert captured["gym"] == gym
    assert captured["member_id"] == first.member_id
    assert captured["status"] == "BOOKED"
    assert captured["view"] == "list"
    assert captured["trainer_staff_profile_id"] is None
    rows = result["results"]
    assert isinstance(rows, list)
    assert {row["member_number"] for row in rows} == {"MEM-000001", "MEM-000002"}
    assert {row["member_name"] for row in rows} == {"Aung Aung"}
    assert result["ambiguous"] is True
    assert result["has_more"] is True
    assert rows[0]["class_occurrence_id"] == str(occurrence.id)
    assert rows[0]["class_name"] == "Yoga"
    assert rows[0]["local_date"] == "2026-11-12"
    assert rows[0]["start_time"] == "00:15"
    assert set(rows[0]) == _BOOKING_KEYS
    assert _HIDDEN.isdisjoint(rows[0])


def test_one_booking_name_is_not_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    occurrence = _occurrence()
    row = _booking(member=_member(uuid4(), "Hla", "MEM-000004"), occurrence=occurrence)
    monkeypatch.setattr(
        "apps.assistant.class_reads.page_class_bookings",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr("apps.assistant.class_reads._detailed_bookings", lambda _gym, rows: rows)
    result = search_bookings(_call(_gym(), {}))
    assert result["ambiguous"] is False
    assert result["results"][0]["member_name"] == "Hla Aung"


def test_booking_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"member_id": "not-a-member"}))
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"occurrence_id": "not-an-occurrence"}))
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"date_from": "2026-11-12", "date_to": "2026-11-11"}))
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"view": "calendar"}))


def test_occurrence_booking_pages_follow_the_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    occurrence = _occurrence()
    first = _booking(member=_member(uuid4(), "Aung", "MEM-000001"), occurrence=occurrence)
    second = _booking(member=_member(uuid4(), "Aung", "MEM-000002"), occurrence=occurrence)
    ordered = sorted((first, second), key=lambda row: row.id)

    monkeypatch.setattr(
        "apps.assistant.class_reads.get_visible_occurrence",
        lambda **_kwargs: occurrence,
    )
    monkeypatch.setattr(
        "apps.assistant.class_reads._visible_occurrence_bookings",
        lambda *_args, **_kwargs: _Rows(list(ordered)),
    )
    monkeypatch.setattr("apps.assistant.class_reads._detailed_bookings", lambda _gym, rows: rows)
    actor = _actor(gym)
    opened = search_bookings(
        _call(
            gym,
            {"occurrence_id": str(occurrence.id), "status": "BOOKED", "limit": 1},
            actor,
        )
    )
    assert opened["has_more"] is True
    assert opened["results"][0]["id"] == str(ordered[0].id)
    cursor = opened["next_after"]
    assert isinstance(cursor, str)
    followed = search_bookings(
        _call(
            gym,
            {
                "occurrence_id": str(occurrence.id),
                "status": "BOOKED",
                "limit": 1,
                "after": cursor,
            },
            actor,
        )
    )
    assert followed["results"][0]["id"] == str(ordered[1].id)
    assert followed["has_more"] is False
    assert followed["ambiguous"] is False
    with pytest.raises(ValidationError):
        search_bookings(_call(gym, {"occurrence_id": str(occurrence.id), "after": "not-a-cursor"}))
    with pytest.raises(ValidationError):
        search_bookings(
            _call(_gym(), {"occurrence_id": str(occurrence.id), "limit": 1, "after": cursor})
        )

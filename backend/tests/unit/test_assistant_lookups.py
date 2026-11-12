from __future__ import annotations

from datetime import date, datetime
from typing import cast
from zoneinfo import ZoneInfo

from apps.assistant.lookups import (
    NamedRecord,
    _keep_stated_dates,
    _local_today,
    _normalize_line_numbers,
    _normalize_numbers,
    _service_day,
    assignment_gap,
    booking_package_gap,
    choose_reference,
    existing_assignment,
    resolve_references,
    unmetered_gate,
)
from apps.tenants.models import Gym

AUNG = NamedRecord(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "aung aung",
    label="Aung Aung",
    code="MEM-000101",
)
AUNG_TWO = NamedRecord(
    "11111111-1111-1111-1111-111111111111",
    "aung aung",
    label="Aung Aung",
    code="MEM-000102",
)
HLA = NamedRecord("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "hla win", label="Hla Win")
MAYA = NamedRecord(
    "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "Maya Htun",
    label="Maya Htun",
    code="MAYA_HTUN",
)
MAYA_TWO = NamedRecord(
    "22222222-2222-2222-2222-222222222222",
    "Maya Htun",
    label="Maya Htun",
    code="MAYA_TWO",
)
TODAY = NamedRecord(
    "dddddddd-dddd-dddd-dddd-dddddddddddd",
    "Desk Cycle",
    when="2026-11-11",
    label="Desk Cycle 2026-11-11",
)
NEXT = NamedRecord(
    "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    "Desk Cycle",
    when="2026-11-18",
    label="Desk Cycle 2026-11-18",
)


def test_a_copied_member_id_is_replaced_by_the_name_the_staff_said() -> None:
    choice = choose_reference(HLA.id, (AUNG, HLA), "Aung Aung ကို Desk PT ရောင်း")

    assert choice.id == AUNG.id
    assert choice.label == "Aung Aung (MEM-000101)"
    assert choice.error == ""


def test_a_trainer_name_resolves_to_one_profile() -> None:
    choice = choose_reference("Maya Htun", (MAYA,), "trainer Maya Htun")

    assert choice.id == MAYA.id
    assert choice.label == "Maya Htun (MAYA_HTUN)"


def test_a_shorter_name_does_not_steal_a_longer_match() -> None:
    short = NamedRecord("ffffffff-ffff-ffff-ffff-ffffffffffff", "aung", label="Aung")
    choice = choose_reference(short.id, (short, AUNG), "Aung Aung ကို ရောင်း")

    assert choice.id == AUNG.id


def test_a_class_without_a_date_stays_unresolved() -> None:
    choice = choose_reference(TODAY.id, (TODAY, NEXT), "Aung Aung ကို Desk Cycle မှာ book")

    assert choice.error == "ambiguous"
    assert {record.when for record in choice.matches} == {"2026-11-11", "2026-11-18"}


def test_a_named_class_date_selects_that_occurrence() -> None:
    choice = choose_reference("Desk Cycle", (TODAY, NEXT), "Desk Cycle ကို 2026-11-18 မှာ book")

    assert choice.id == NEXT.id
    assert choice.label == "Desk Cycle 2026-11-18"


def test_an_unknown_name_is_not_found() -> None:
    choice = choose_reference("No Such Person", (AUNG, HLA), "No Such Person")

    assert choice.error == "not_found"


def test_a_membership_plan_does_not_need_a_trainer_assignment() -> None:
    assert assignment_gap(cast(Gym, None), "create_plan", {}) is None


def test_an_unstated_assignment_date_is_not_replaced() -> None:
    payload: dict[str, object] = {"effective_from": "2025-01-24", "member_id": "aung"}

    field = _keep_stated_dates(payload, "Aung Aung ကို Maya Htun နဲ့ Desk PT ရောင်း")

    assert field == "effective_from"
    assert payload["effective_from"] == "2025-01-24"


def test_a_date_the_staff_typed_stays() -> None:
    payload: dict[str, object] = {"effective_from": "2026-11-18"}

    assert _keep_stated_dates(payload, "2026-11-18 ကစပြီး တာဝန်ပေး") is None

    assert payload["effective_from"] == "2026-11-18"


def test_a_wrong_model_date_follows_the_date_the_staff_typed() -> None:
    payload: dict[str, object] = {"effective_from": "2025-01-24"}

    assert _keep_stated_dates(payload, "2026-11-18 ကစပြီး တာဝန်ပေး") is None

    assert payload["effective_from"] == "2026-11-18"


def test_the_next_burmese_day_uses_the_gym_day() -> None:
    payload: dict[str, object] = {"effective_from": "2025-01-24"}

    assert _keep_stated_dates(payload, "နောက်တစ်နေ့ ကစပြီး", today=date(2026, 11, 11)) is None

    assert payload["effective_from"] == "2026-11-12"


def test_tomorrow_in_burmese_is_the_next_gym_day() -> None:
    payload: dict[str, object] = {"starts_at": "2025-08-07T09:30:00+06:30"}

    assert _keep_stated_dates(payload, "မနက်ဖြန် မနက် ၉ နာရီခွဲ", today=date(2026, 11, 11)) is None

    assert payload["starts_at"] == "2026-11-12T09:30:00+06:30"


def test_a_clock_time_without_a_day_does_not_become_today() -> None:
    payload: dict[str, object] = {"starts_at": "2025-08-07T09:30:00+06:30"}

    field = _keep_stated_dates(payload, "မနက် ၉ နာရီခွဲ", today=date(2026, 11, 11))

    assert field == "starts_at"
    assert payload["starts_at"] == "2025-08-07T09:30:00+06:30"


def test_today_in_burmese_keeps_the_clock_time() -> None:
    payload: dict[str, object] = {"ends_at": "2025-01-24 10:00:00"}

    assert _keep_stated_dates(payload, "ဒီနေ့ မနက် ၉ နာရီ", today=date(2026, 11, 11)) is None

    assert payload["ends_at"] == "2026-11-11 10:00:00"


def test_the_gym_timezone_picks_the_local_day(monkeypatch) -> None:
    moment = datetime(2026, 11, 11, 20, 30, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr("apps.assistant.lookups.timezone.now", lambda: moment)

    assert _local_today(cast(object, type("Gym", (), {"timezone": "Asia/Yangon"})())) == date(
        2026, 11, 12
    )


def test_two_members_with_one_name_are_not_chosen_by_a_model_id() -> None:
    choice = choose_reference(
        AUNG.id,
        (AUNG, AUNG_TWO),
        "Aung Aung ကို Maya Htun နဲ့ PT assignment လုပ်ပေးပါ။",
    )

    assert choice.id == ""
    assert choice.error == "ambiguous"
    assert {record.code for record in choice.matches} == {"MEM-000101", "MEM-000102"}
    assert "MEM-000101" in choice.matches[0].shown()


def test_a_member_number_in_the_message_is_found_when_the_model_sends_the_number() -> None:
    choice = choose_reference("MEM-000102", (AUNG, AUNG_TWO), "MEM-000102 ကို ရွေးပါ")

    assert choice.id == AUNG_TWO.id
    assert choice.error == ""


def test_a_model_member_number_does_not_choose_when_staff_only_said_the_name() -> None:
    choice = choose_reference("MEM-000101", (AUNG, AUNG_TWO), "Aung Aung ကို တာဝန်ပေး")

    assert choice.error == "ambiguous"
    assert choice.id == ""


def test_an_unstated_clock_time_is_not_kept() -> None:
    payload: dict[str, object] = {"starts_at": "2026-11-12T10:00:00+06:30"}

    field = _keep_stated_dates(payload, "မနက်ဖြန် booking လုပ်ပေးပါ", today=date(2026, 11, 11))

    assert field == "time:starts_at"
    assert payload["starts_at"] == "2026-11-12T10:00:00+06:30"


def test_a_member_number_selects_that_person_when_the_model_picked_the_other() -> None:
    choice = choose_reference(
        AUNG.id,
        (AUNG, AUNG_TWO),
        "MEM-000102 ဖြစ်တဲ့ Aung Aung ကို ရွေးပါ",
    )

    assert choice.id == AUNG_TWO.id
    assert choice.error == ""
    assert "MEM-000102" in choice.label


def test_two_trainers_with_one_name_stay_unresolved() -> None:
    choice = choose_reference(MAYA.id, (MAYA, MAYA_TWO), "Maya Htun နဲ့ booking လုပ်ပေးပါ")

    assert choice.error == "ambiguous"
    assert {record.code for record in choice.matches} == {"MAYA_HTUN", "MAYA_TWO"}


def test_a_unique_name_still_selects_that_record() -> None:
    choice = choose_reference("Hla Win", (AUNG, AUNG_TWO, HLA), "Hla Win ကို တာဝန်ပေး")

    assert choice.id == HLA.id
    assert choice.error == ""


def test_myanmar_digits_in_an_amount_become_arabic() -> None:
    payload: dict[str, object] = {"gross_fee": "၈,၀၀၀"}  # noqa: RUF001

    _normalize_numbers(payload)

    assert payload["gross_fee"] == "8000"


def test_a_sale_does_not_need_a_booking_package() -> None:
    assert booking_package_gap(cast(Gym, None), "create_pt_sale", {}) is None


def test_an_unmetered_booking_does_not_need_a_package() -> None:
    assert (
        booking_package_gap(
            cast(Gym, None),
            "create_pt_booking",
            {"unmetered": True, "member_id": "aung aung"},
        )
        is None
    )


def test_a_sale_is_not_treated_as_a_duplicate_assignment() -> None:
    assert existing_assignment(cast(Gym, None), "create_pt_sale", {}) is None


def test_an_assignment_without_a_trainer_id_is_not_a_duplicate() -> None:
    assert (
        existing_assignment(
            cast(Gym, None),
            "create_assignment",
            {"effective_from": "2026-11-11", "member_id": "aung aung"},
        )
        is None
    )


WATER = NamedRecord(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
    "spring water",
    label="Spring Water",
    code="WATER-A",
)
WATER_TWO = NamedRecord(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02",
    "spring water",
    label="Spring Water",
    code="WATER-B",
)
MONTHLY = NamedRecord(
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01",
    "monthly",
    label="Monthly",
    code="GOLD",
)
MONTHLY_TWO = NamedRecord(
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02",
    "monthly",
    label="Monthly",
    code="SILVER",
)
DESK = NamedRecord(
    "cccccccc-cccc-cccc-cccc-cccccccccc01",
    "desk eight",
    label="Desk Eight",
    code="PT8A",
)
DESK_TWO = NamedRecord(
    "cccccccc-cccc-cccc-cccc-cccccccccc02",
    "desk eight",
    label="Desk Eight",
    code="PT8B",
)


def test_one_product_name_selects_that_sku() -> None:
    choice = choose_reference("Spring Water", (WATER,), "Spring Water လိုချင်ပါတယ်")

    assert choice.id == WATER.id
    assert choice.error == ""
    assert choice.label == "Spring Water (WATER-A)"


def test_two_products_with_one_name_are_not_chosen_by_a_model_id() -> None:
    choice = choose_reference(WATER.id, (WATER, WATER_TWO), "Spring Water ယူမယ်")

    assert choice.id == ""
    assert choice.error == "ambiguous"
    assert {record.code for record in choice.matches} == {"WATER-A", "WATER-B"}


def test_a_product_sku_in_the_message_selects_that_row() -> None:
    choice = choose_reference(WATER.id, (WATER, WATER_TWO), "WATER-B ကို ရွေးပါ")

    assert choice.id == WATER_TWO.id
    assert choice.error == ""
    assert "WATER-B" in choice.label


def test_two_plans_with_one_name_are_not_chosen_by_a_model_id() -> None:
    choice = choose_reference(MONTHLY.id, (MONTHLY, MONTHLY_TWO), "Monthly plan တစ်ခု")

    assert choice.error == "ambiguous"
    assert choice.id == ""
    assert {record.code for record in choice.matches} == {"GOLD", "SILVER"}


def test_a_plan_code_selects_that_plan_when_the_model_picked_the_other() -> None:
    choice = choose_reference(MONTHLY_TWO.id, (MONTHLY, MONTHLY_TWO), "plan code GOLD")

    assert choice.id == MONTHLY.id
    assert choice.error == ""


def test_one_package_name_selects_that_package() -> None:
    choice = choose_reference("Desk Eight", (DESK,), "Desk Eight package")

    assert choice.id == DESK.id
    assert choice.error == ""
    assert choice.label == "Desk Eight (PT8A)"


def test_two_packages_with_one_name_are_not_chosen_by_a_model_id() -> None:
    choice = choose_reference(DESK.id, (DESK, DESK_TWO), "Desk Eight လိုချင်ပါတယ်")

    assert choice.error == "ambiguous"
    assert choice.id == ""
    assert {record.code for record in choice.matches} == {"PT8A", "PT8B"}


def test_a_package_code_selects_that_package() -> None:
    choice = choose_reference("PT8B", (DESK, DESK_TWO), "package code PT8B")

    assert choice.id == DESK_TWO.id
    assert choice.error == ""


def test_catalog_lookup_without_permission_does_not_load_rows(monkeypatch) -> None:
    def loaded(_gym: object) -> tuple[NamedRecord, ...]:
        raise AssertionError("catalog rows were loaded")

    monkeypatch.setattr("apps.assistant.lookups._products", loaded)
    monkeypatch.setattr("apps.assistant.lookups._plans", loaded)
    monkeypatch.setattr("apps.assistant.lookups._service_packages", loaded)
    payload = {
        "product_id": "Spring Water",
        "plan_id": "Monthly",
        "service_package_id": "Desk Eight",
        "lines": [{"product_id": "Spring Water", "quantity": "1"}],
    }

    returned, labels, failure = resolve_references(
        cast(Gym, None),
        cast(object, None),  # type: ignore[arg-type]
        payload,
        permission_codes=frozenset({"gym.view"}),
    )

    assert failure == {"error": "permission_denied", "field": "product_id"}
    assert labels == {}
    assert returned["product_id"] == "Spring Water"
    assert "WATER-A" not in str(failure)
    _returned, _labels, plan_failure = resolve_references(
        cast(Gym, None),
        cast(object, None),  # type: ignore[arg-type]
        {"plan_id": "Monthly"},
        permission_codes=frozenset({"gym.view"}),
    )
    _returned, _labels, package_failure = resolve_references(
        cast(Gym, None),
        cast(object, None),  # type: ignore[arg-type]
        {"service_package_id": "Desk Eight"},
        permission_codes=frozenset({"gym.view"}),
    )
    assert plan_failure == {"error": "permission_denied", "field": "plan_id"}
    assert package_failure == {"error": "permission_denied", "field": "service_package_id"}


def test_an_unstated_start_date_is_not_invented() -> None:
    payload: dict[str, object] = {"start_date": "2026-11-12"}

    field = _keep_stated_dates(payload, "Monthly plan ရောင်းပေးပါ")

    assert field == "start_date"
    assert payload["start_date"] == "2026-11-12"


def test_a_stated_start_date_replaces_the_model_date() -> None:
    payload: dict[str, object] = {"start_date": "2025-01-01", "business_date": "2025-01-02"}

    assert _keep_stated_dates(payload, "2026-11-18 က စမယ်") is None

    assert payload["start_date"] == "2026-11-18"
    assert payload["business_date"] == "2026-11-18"


def test_a_stated_clock_replaces_the_model_clock() -> None:
    gym = cast(Gym, type("Gym", (), {"timezone": "Asia/Yangon"})())
    payload: dict[str, object] = {
        "starts_at": "2026-11-12T10:15:00+06:30",
        "ends_at": "2026-11-12T11:15:00+06:30",
    }

    assert _keep_stated_dates(payload, "2026-11-12 9:00 to 10:00", gym=gym) is None

    assert str(payload["starts_at"]).startswith("2026-11-12T09:00:00")
    assert str(payload["ends_at"]).startswith("2026-11-12T10:00:00")


def test_a_utc_timestamp_near_midnight_uses_the_gym_day() -> None:
    gym = cast(Gym, type("Gym", (), {"timezone": "Asia/Yangon"})())
    raw = "2026-11-11T18:30:00+00:00"
    payload: dict[str, object] = {"starts_at": raw, "ends_at": "2026-11-11T19:30:00+00:00"}

    assert _service_day(raw, gym) == date(2026, 11, 12)
    assert _keep_stated_dates(payload, "2026-11-12 မနက် ၉ နာရီ မှ ၁၀ နာရီ", gym=gym) is None
    assert str(payload["starts_at"]).startswith("2026-11-12T09:00:00")
    assert str(payload["ends_at"]).startswith("2026-11-12T10:00:00")
    assert "+06:30" in str(payload["starts_at"])


def test_one_stated_clock_does_not_invent_the_end() -> None:
    payload: dict[str, object] = {
        "starts_at": "2026-11-12T09:00:00+06:30",
        "ends_at": "2026-11-12T10:00:00+06:30",
    }

    field = _keep_stated_dates(payload, "2026-11-12 မနက် ၉ နာရီ")

    assert field == "time:ends_at"
    assert str(payload["starts_at"]).startswith("2026-11-12T09:00:00")


def test_a_model_unmetered_flag_is_removed_without_staff_intent() -> None:
    payload: dict[str, object] = {"unmetered": True, "unmetered_reason": "invented"}

    assert (
        unmetered_gate(
            cast(Gym, None),
            None,
            "create_pt_booking",
            payload,
            "မနက်ဖြန် ၉ နာရီ booking လုပ်ပေးပါ",
        )
        is None
    )
    assert "unmetered" not in payload
    assert "unmetered_reason" not in payload


def test_unmetered_wording_without_an_actor_is_refused() -> None:
    payload: dict[str, object] = {"unmetered": True}

    refused = unmetered_gate(
        cast(Gym, None),
        None,
        "create_class_booking",
        payload,
        "unmetered booking because the guest has no package",
    )

    assert refused == {"error": "unmetered_denied"}


def test_myanmar_digits_in_a_line_quantity_become_arabic() -> None:
    payload: dict[str, object] = {
        "lines": [{"product_id": "water", "quantity": "၂", "discount": "၀"}]  # noqa: RUF001
    }

    _normalize_line_numbers(payload)

    lines = payload["lines"]
    assert isinstance(lines, list)
    assert lines[0]["quantity"] == "2"
    assert lines[0]["discount"] == "0"


def test_a_sale_without_a_trainer_id_is_not_an_assignment_gap() -> None:
    assert (
        assignment_gap(
            cast(Gym, None),
            "create_pt_sale",
            {"service_date": "2026-11-11", "member_id": "aung aung"},
        )
        is None
    )

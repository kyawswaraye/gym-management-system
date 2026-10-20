"""Own trainer dashboard and session report reads.

Authorization and fixture setup stay outside the query captures. The dashboard
selector is three queries and the session selector is two, for one day and for
366 days. The dashboard handler adds the single gym-settings read.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from apps.common.db.tenant_context import set_gym_context
from apps.reports.selectors import (
    TRAINER_DASHBOARD_QUERY_COUNT,
    TRAINER_SESSION_REPORT_QUERY_COUNT,
    TRAINER_SESSION_SOURCE_STATUS_POLICY,
    TRAINER_UPCOMING_LIMIT,
    trainer_dashboard,
    trainer_session_report,
)
from apps.reports.views import TrainerDashboardView
from apps.tenants.models import Gym, GymMembershipRole, Role, RolePermission, StaffProfile
from tests.security.test_bookings import _key
from tests.security.test_dashboard import _headers, _staff, _zone
from tests.security.test_retail import _enable, _gym
from tests.security.test_session_reports import _disable
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings
from tests.security.test_trainer_assignments import _assign, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

LA = ZoneInfo("America/Los_Angeles")
FROZEN = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
TODAY = date(2026, 10, 19)
DASHBOARD = "/api/v1/trainer/dashboard/"
SESSIONS = "/api/v1/trainer/reports/sessions/"
COUNT_FIELDS = (
    "bookings",
    "unique_members",
    "booked_sessions",
    "attended_sessions",
    "no_show_sessions",
    "cancelled_sessions",
)
DASHBOARD_FIELDS = {
    "timezone",
    "today",
    "range",
    "generated_at",
    "trainer_staff_profile_id",
    "active_assigned_members",
    "sessions",
    "upcoming_bookings",
}
UPCOMING_FIELDS = {
    "id",
    "member_id",
    "member_number",
    "full_name",
    "starts_at",
    "ends_at",
}
SESSION_FIELDS = {
    "gym",
    "timezone",
    "range",
    "generated_at",
    "source_status_policy",
    "rows",
    "totals",
}
SECRET_EMAIL = "nila.secret@example.test"
SECRET_PHONE = "+15551230001"
SECRET_EMERGENCY = "+15559870001"
SECRET_GUARDIAN = "Secret Guardian"
SECRET_PROFILE_PHONE = "+15557650000"
SECRET_COMMISSION = "17.50"
SECRET_PRICE = "91.91"
LEAKS = (
    SECRET_EMAIL,
    SECRET_PHONE,
    SECRET_EMERGENCY,
    SECRET_GUARDIAN,
    SECRET_PROFILE_PHONE,
    SECRET_COMMISSION,
    SECRET_PRICE,
)


def _freeze(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    monkeypatch.setattr(timezone, "now", lambda: instant)


def _counts(source: dict[str, Any]) -> dict[str, int]:
    return {field: source[field] for field in COUNT_FIELDS}


def _zero_counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_FIELDS, 0)


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA)


def _report(api: APIClient, headers: dict[str, str], path: str) -> tuple[dict[str, Any], str]:
    response = api.get(path, **headers)
    assert response.status_code == 200, response.content
    rendered = response.content.decode()
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data, rendered


def _row(report: dict[str, Any], day: str) -> dict[str, Any]:
    rows = report["rows"]
    assert isinstance(rows, list)
    matches = [row for row in rows if isinstance(row, dict) and row["date"] == day]
    assert len(matches) == 1
    return matches[0]


def _person(
    api: APIClient,
    headers: dict[str, str],
    *,
    first_name: str,
    email: str = "",
    phone: str = "",
    emergency_phone: str = "",
) -> dict[str, str]:
    body: dict[str, object] = {
        "first_name": first_name,
        "last_name": "Client",
        "join_date": "2026-10-01",
    }
    if email:
        body["email"] = email
        body["phone"] = phone
        body["emergency_contact_name"] = SECRET_GUARDIAN
        body["emergency_contact_phone"] = emergency_phone
    created = api.post("/api/v1/members/", body, format="json", **headers)
    assert created.status_code == 201, created.json()
    data = created.json()["data"]
    assert isinstance(data, dict)
    return {
        "id": str(data["id"]),
        "member_number": str(data["member_number"]),
        "full_name": f"{data['first_name']} {data['last_name']}",
    }


def _assignment(
    api: APIClient,
    headers: dict[str, str],
    *,
    trainer_id: str,
    member_id: str,
    start: str,
    until: str | None = None,
) -> str:
    created = _assign(
        api,
        headers,
        trainer_id=trainer_id,
        member_id=member_id,
        start=start,
        until=until,
    )
    assert created.status_code == 201, created.json()
    data = created.json()["data"]
    assert isinstance(data, dict)
    return str(data["id"])


def _post(api: APIClient, headers: dict[str, str], path: str, body: dict[str, object]) -> str:
    created = api.post(path, body, format="json", **headers, **_key("trainer-report"))
    assert created.status_code in {200, 201}, created.json()
    data = created.json()["data"]
    assert isinstance(data, dict)
    return str(data["id"])


def _book(
    api: APIClient,
    headers: dict[str, str],
    *,
    trainer_id: str,
    member_id: str,
    moment: datetime,
) -> str:
    return _post(
        api,
        headers,
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": trainer_id,
            "member_id": member_id,
            "starts_at": moment.isoformat(),
            "ends_at": (moment + timedelta(minutes=20)).isoformat(),
            "unmetered": True,
            "unmetered_reason": "Owner coverage",
        },
    )


def _statuses(api: APIClient, headers: dict[str, str], path: str) -> int:
    response = api.get(path, **headers)
    return response.status_code


def _queries_mentioning(captured: CaptureQueriesContext, table: str) -> int:
    return sum(table in query["sql"].lower() for query in captured.captured_queries)


def _handler(
    gym: Gym,
    profile_id: Any,
    params: dict[str, str],
) -> tuple[CaptureQueriesContext, dict[str, Any]]:
    request = Request(APIRequestFactory().get(DASHBOARD, params))
    request.gym = gym
    request.trainer_staff_profile_id = profile_id
    with CaptureQueriesContext(connection) as captured:
        response = TrainerDashboardView().get(request)
    assert response.status_code == 200
    data = response.data["data"]
    assert isinstance(data, dict)
    return captured, data


@override_settings(**jwt_settings())
def test_trainer_reports_count_own_rows_and_keep_read_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert FROZEN.astimezone(LA) == _local(TODAY, 23)
    assert FROZEN.astimezone(LA).utcoffset() == timedelta(hours=-7)
    early_boundary = _local(date(2026, 10, 17), 23, 30)
    late_boundary = _local(date(2026, 10, 18), 17)
    assert early_boundary.astimezone(UTC).date() == date(2026, 10, 18)
    assert late_boundary.astimezone(UTC) == datetime(2026, 10, 19, 0, tzinfo=UTC)
    _freeze(monkeypatch, FROZEN)

    api, gym, _owner, owner_headers = _gym("TRNRPT")
    other_api, other, _other_owner, other_headers = _gym("TRNOTH")
    for tenant in (gym, other):
        _enable(tenant, "members")
        _enable(tenant, "pt")
        _zone(tenant, "America/Los_Angeles", 0)
    ada_membership = add_staff(gym, email="trnrpt-ada@example.test", role_key="TRAINER")
    bea_membership = add_staff(gym, email="trnrpt-bea@example.test", role_key="TRAINER")
    lead_membership = add_staff(gym, email="trnrpt-lead@example.test", role_key="TRAINER")
    away_membership = add_staff(gym, email="trnrpt-away@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=lead_membership,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
    ada = _profile(gym, ada_membership, code="TR-ADA", name="Ada Trainer")
    bea = _profile(gym, bea_membership, code="TR-BEA", name="Bea Trainer")
    lead = _profile(gym, lead_membership, code="TR-LEAD", name="Lead Trainer")
    _profile(
        gym,
        away_membership,
        code="TR-AWAY",
        name="Away Trainer",
        status=StaffProfile.Status.ON_LEAVE,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(
            phone=SECRET_PROFILE_PHONE,
            default_commission_percent=SECRET_COMMISSION,
        )
    other_membership = add_staff(other, email="trnoth-zoe@example.test", role_key="TRAINER")
    other_trainer = _profile(other, other_membership, code="TR-ZOE", name="Zoe Trainer")
    ada_headers = _headers(ada_membership)
    bea_headers = _headers(bea_membership)
    lead_headers = _headers(lead_membership)
    other_trainer_headers = _headers(other_membership)
    staff = _staff(gym)
    priced = api.post(
        "/api/v1/service-packages/",
        {
            "type": "PT",
            "code": "PT_SECRET",
            "name": "Secret package",
            "session_count": 2,
            "validity_value": 12,
            "validity_unit": "MONTH",
            "price": SECRET_PRICE,
        },
        format="json",
        **owner_headers,
    )
    assert priced.status_code == 201, priced.json()

    dash_rejected = (
        "",
        "?range=",
        "?range=yesterday",
        "?range=today&date_from=2026-10-19&date_to=2026-10-19",
        "?date_from=2026-10-19",
        "?date_to=2026-10-19",
        "?date_from=2026-10-20&date_to=2026-10-19",
        "?date_from=2024-01-01&date_to=2025-01-01",
        "?date_from=&date_to=2026-10-19",
        f"?range=today&gym_id={gym.id}",
        "?date_from=2026-10-19&date_to=2026-10-19&status=BOOKED",
    )
    session_rejected = (
        "",
        "?date_from=2026-10-18",
        "?date_from=2026-10-19&date_to=2026-10-18",
        "?date_from=2024-01-01&date_to=2025-01-01",
        "?date_from=1899-12-31&date_to=1899-12-31",
        "?date_from=2100-01-01&date_to=2100-01-01",
        "?date_from=&date_to=2026-10-18",
        "?date_from=2026-10-18&date_from=2026-10-19&date_to=2026-10-19",
        f"?date_from=2024-02-28&date_to=2024-03-01&gym_id={gym.id}&status=BOOKED",
    )
    for query in dash_rejected:
        assert _statuses(api, ada_headers, f"{DASHBOARD}{query}") == 400
    for query in session_rejected:
        assert _statuses(api, ada_headers, f"{SESSIONS}{query}") == 400
    assert _statuses(api, _headers(staff["reception"]), f"{DASHBOARD}?range=yesterday") == 404

    nila = _person(
        api,
        owner_headers,
        first_name="Nila",
        email=SECRET_EMAIL,
        phone=SECRET_PHONE,
        emergency_phone=SECRET_EMERGENCY,
    )
    omar = _person(api, owner_headers, first_name="Omar")
    pia = _person(api, owner_headers, first_name="Pia")
    quin = _person(api, owner_headers, first_name="Quin")
    rin = _person(api, owner_headers, first_name="Rin")
    sia = _person(api, owner_headers, first_name="Sia")
    tess = _person(api, owner_headers, first_name="Tess")
    zoe = _person(other_api, other_headers, first_name="Zoe")
    _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        start="2026-10-01",
    )
    _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=omar["id"],
        start="2026-10-01",
        until="2026-10-19",
    )
    _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=pia["id"],
        start="2026-10-01",
        until="2026-10-18",
    )
    quin_assignment = _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=quin["id"],
        start="2026-10-01",
    )
    rin_assignment = _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=rin["id"],
        start="2026-10-01",
    )
    _assignment(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=sia["id"],
        start="2026-10-20",
    )
    _assignment(
        api,
        owner_headers,
        trainer_id=str(bea.id),
        member_id=tess["id"],
        start="2026-10-01",
    )
    _assignment(
        other_api,
        other_headers,
        trainer_id=str(other_trainer.id),
        member_id=zoe["id"],
        start="2026-10-01",
    )
    _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=quin["id"],
        moment=_local(date(2026, 10, 17), 10),
    )
    attended = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        moment=early_boundary,
    )
    no_show = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=pia["id"],
        moment=_local(date(2026, 10, 18), 10),
    )
    cancelled = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        moment=late_boundary,
    )
    _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=omar["id"],
        moment=_local(TODAY, 10),
    )
    _post(api, owner_headers, f"/api/v1/pt-bookings/{attended}/attend/", {})
    _post(api, owner_headers, f"/api/v1/pt-bookings/{no_show}/no-show/", {})
    _post(
        api,
        owner_headers,
        f"/api/v1/pt-bookings/{cancelled}/cancel/",
        {"reason": "Member cancelled"},
    )
    ended = api.post(
        f"/api/v1/trainer-assignments/{quin_assignment}/end/",
        {"effective_until": "2026-10-17", "reason": "Program finished"},
        format="json",
        **owner_headers,
    )
    assert ended.status_code == 200, ended.json()
    cancelled_assignment = api.post(
        f"/api/v1/trainer-assignments/{rin_assignment}/cancel/",
        {"reason": "Booked by mistake"},
        format="json",
        **owner_headers,
    )
    assert cancelled_assignment.status_code == 200, cancelled_assignment.json()
    _book(
        api,
        owner_headers,
        trainer_id=str(bea.id),
        member_id=tess["id"],
        moment=_local(date(2026, 10, 18), 12),
    )
    _book(
        other_api,
        other_headers,
        trainer_id=str(other_trainer.id),
        member_id=zoe["id"],
        moment=_local(date(2026, 10, 18), 12),
    )

    before_window = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        moment=_local(TODAY, 22),
    )
    anchor = _local(TODAY, 23)
    series: list[tuple[datetime, str]] = []
    for index in range(10, -1, -1):
        moment = anchor + timedelta(minutes=30 * index)
        series.append(
            (
                moment,
                _book(
                    api,
                    owner_headers,
                    trainer_id=str(ada.id),
                    member_id=nila["id"],
                    moment=moment,
                ),
            )
        )
    series.sort(key=lambda item: item[0])
    late = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        moment=_local(date(2026, 10, 25), 23),
    )
    at_bound = _book(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=nila["id"],
        moment=_local(date(2026, 10, 26), 0),
    )
    in_window = [*series, (_local(date(2026, 10, 25), 23), late)]
    expected_ids = [
        booking_id for _starts, booking_id in sorted(in_window, key=lambda item: (item[0], item[1]))
    ][:TRAINER_UPCOMING_LIMIT]
    excluded_ids = {series[10][1], late, before_window, at_bound}
    assert excluded_ids.isdisjoint(expected_ids)
    assert len(expected_ids) == TRAINER_UPCOMING_LIMIT

    historical, historical_body = _report(
        api,
        ada_headers,
        f"{SESSIONS}?date_from=2026-10-16&date_to=2026-10-18",
    )
    assert set(historical) == SESSION_FIELDS
    assert historical["source_status_policy"] == TRAINER_SESSION_SOURCE_STATUS_POLICY
    assert historical["timezone"] == "America/Los_Angeles"
    assert historical["gym"]["id"] == str(gym.id)
    assert historical["gym"]["code"] == gym.code
    assert "currency" not in historical
    assert [row["date"] for row in historical["rows"]] == [
        "2026-10-16",
        "2026-10-17",
        "2026-10-18",
    ]
    for row in historical["rows"]:
        assert set(row) == set(COUNT_FIELDS) | {"date"}
        assert row["bookings"] == (
            row["booked_sessions"]
            + row["attended_sessions"]
            + row["no_show_sessions"]
            + row["cancelled_sessions"]
        )
    assert _counts(_row(historical, "2026-10-16")) == _zero_counts()
    assert _counts(_row(historical, "2026-10-17")) == {
        "bookings": 2,
        "unique_members": 2,
        "booked_sessions": 1,
        "attended_sessions": 1,
        "no_show_sessions": 0,
        "cancelled_sessions": 0,
    }
    assert _counts(_row(historical, "2026-10-18")) == {
        "bookings": 2,
        "unique_members": 2,
        "booked_sessions": 0,
        "attended_sessions": 0,
        "no_show_sessions": 1,
        "cancelled_sessions": 1,
    }
    assert _counts(historical["totals"]) == {
        "bookings": 4,
        "unique_members": 3,
        "booked_sessions": 1,
        "attended_sessions": 1,
        "no_show_sessions": 1,
        "cancelled_sessions": 1,
    }
    assert sum(row["unique_members"] for row in historical["rows"]) == 4
    assert "2026-10-20T06:00:00" in historical["generated_at"]
    for secret in (*LEAKS, tess["id"], zoe["id"], "Tess", "Zoe"):
        assert secret not in historical_body

    leap, _leap_body = _report(
        api,
        ada_headers,
        f"{SESSIONS}?date_from=2024-02-28&date_to=2024-03-01",
    )
    assert [row["date"] for row in leap["rows"]] == ["2024-02-28", "2024-02-29", "2024-03-01"]
    assert all(_counts(row) == _zero_counts() for row in leap["rows"])
    full_year, _year_body = _report(
        api,
        ada_headers,
        f"{SESSIONS}?date_from=2024-01-01&date_to=2024-12-31",
    )
    assert len(full_year["rows"]) == 366
    assert full_year["rows"][0]["date"] == "2024-01-01"
    assert full_year["rows"][-1]["date"] == "2024-12-31"
    assert _counts(full_year["totals"]) == _zero_counts()

    custom, custom_body = _report(
        api,
        ada_headers,
        f"{DASHBOARD}?date_from=2026-10-16&date_to=2026-10-18",
    )
    assert set(custom) == DASHBOARD_FIELDS
    assert custom["timezone"] == "America/Los_Angeles"
    assert custom["today"] == TODAY.isoformat()
    assert custom["range"] == {"date_from": "2026-10-16", "date_to": "2026-10-18"}
    assert custom["trainer_staff_profile_id"] == str(ada.id)
    assert custom["active_assigned_members"] == 2
    assert custom["generated_at"] == historical["generated_at"]
    assert custom["sessions"] == historical["totals"]
    assert "currency" not in custom
    today_dash, today_body = _report(api, ada_headers, f"{DASHBOARD}?range=today")
    assert today_dash["range"] == {"date_from": "2026-10-19", "date_to": "2026-10-19"}
    assert today_dash["today"] == "2026-10-19"
    assert today_dash["generated_at"] == custom["generated_at"]
    assert today_dash["active_assigned_members"] == 2
    assert today_dash["sessions"] == {
        "bookings": 4,
        "unique_members": 2,
        "booked_sessions": 4,
        "attended_sessions": 0,
        "no_show_sessions": 0,
        "cancelled_sessions": 0,
    }
    upcoming = today_dash["upcoming_bookings"]
    assert [row["id"] for row in upcoming] == expected_ids
    assert [row["starts_at"] for row in upcoming] == sorted(row["starts_at"] for row in upcoming)
    assert len({row["starts_at"] for row in upcoming}) == len(upcoming)
    for row in upcoming:
        assert set(row) == UPCOMING_FIELDS
        assert row["member_id"] == nila["id"]
        assert row["member_number"] == nila["member_number"]
        assert row["full_name"] == "Nila Client"
    assert excluded_ids.isdisjoint({row["id"] for row in upcoming})
    week = _report(api, ada_headers, f"{DASHBOARD}?range=this_week")[0]
    month = _report(api, ada_headers, f"{DASHBOARD}?range=this_month")[0]
    previous = _report(api, ada_headers, f"{DASHBOARD}?range=last_month")[0]
    year = _report(api, ada_headers, f"{DASHBOARD}?range=this_year")[0]
    wide = _report(api, ada_headers, f"{DASHBOARD}?date_from=2024-01-01&date_to=2024-12-31")[0]
    assert week["range"] == {"date_from": "2026-10-18", "date_to": "2026-10-24"}
    assert month["range"] == {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    assert previous["range"] == {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    assert year["range"] == {"date_from": "2026-01-01", "date_to": "2026-12-31"}
    assert wide["range"] == {"date_from": "2024-01-01", "date_to": "2024-12-31"}
    assert previous["sessions"] == _zero_counts()
    assert wide["sessions"] == _zero_counts()
    for payload in (week, month, previous, year, wide):
        assert payload["today"] == "2026-10-19"
        assert payload["active_assigned_members"] == 2
        assert payload["trainer_staff_profile_id"] == str(ada.id)
        assert [row["id"] for row in payload["upcoming_bookings"]] == expected_ids
    for secret in (*LEAKS, tess["id"], zoe["id"], omar["id"], "Omar", "Tess", "Zoe"):
        assert secret not in custom_body
        assert secret not in today_body

    bea_day, bea_body = _report(
        api,
        bea_headers,
        f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18",
    )
    ada_day, _ada_day_body = _report(
        api,
        ada_headers,
        f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18",
    )
    assert _counts(bea_day["totals"]) == {
        "bookings": 1,
        "unique_members": 1,
        "booked_sessions": 1,
        "attended_sessions": 0,
        "no_show_sessions": 0,
        "cancelled_sessions": 0,
    }
    assert _counts(ada_day["totals"]) == {
        "bookings": 2,
        "unique_members": 2,
        "booked_sessions": 0,
        "attended_sessions": 0,
        "no_show_sessions": 1,
        "cancelled_sessions": 1,
    }
    assert nila["member_number"] not in bea_body
    bea_dash, _bea_dash_body = _report(api, bea_headers, f"{DASHBOARD}?range=today")
    assert bea_dash["trainer_staff_profile_id"] == str(bea.id)
    assert bea_dash["active_assigned_members"] == 1
    assert bea_dash["sessions"] == _zero_counts()
    assert bea_dash["upcoming_bookings"] == []
    other_day, _other_body = _report(
        other_api,
        other_trainer_headers,
        f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18",
    )
    assert other_day["gym"]["code"] == "TRNOTH"
    assert _counts(other_day["totals"]) == _counts(bea_day["totals"])
    other_dash, _other_dash_body = _report(
        other_api,
        other_trainer_headers,
        f"{DASHBOARD}?range=today",
    )
    assert other_dash["trainer_staff_profile_id"] == str(other_trainer.id)
    assert other_dash["active_assigned_members"] == 1
    assert nila["id"] not in _other_dash_body
    lead_dash, lead_body = _report(api, lead_headers, f"{DASHBOARD}?range=today")
    assert lead_dash["trainer_staff_profile_id"] == str(lead.id)
    assert lead_dash["active_assigned_members"] == 0
    assert lead_dash["sessions"] == _zero_counts()
    assert lead_dash["upcoming_bookings"] == []
    assert nila["member_number"] not in lead_body
    lead_sessions, _lead_sessions_body = _report(
        api,
        lead_headers,
        f"{SESSIONS}?date_from=2026-10-16&date_to=2026-10-19",
    )
    assert _counts(lead_sessions["totals"]) == _zero_counts()

    for membership in (
        staff["reception"],
        staff["manager"],
        staff["accountant"],
        staff["trainer"],
        away_membership,
    ):
        denied = _headers(membership)
        assert _statuses(api, denied, f"{DASHBOARD}?range=today") == 404
        assert _statuses(api, denied, f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18") == 404
    assert _statuses(api, owner_headers, f"{DASHBOARD}?range=today") == 404
    assert (
        _statuses(api, owner_headers, f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18") == 404
    )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        preset_queries, preset_data = _handler(loaded, ada.id, {"range": "today"})
        wide_queries, wide_data = _handler(
            loaded,
            ada.id,
            {"date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
    assert len(preset_queries.captured_queries) == TRAINER_DASHBOARD_QUERY_COUNT + 1
    assert len(wide_queries.captured_queries) == TRAINER_DASHBOARD_QUERY_COUNT + 1
    for captured in (preset_queries, wide_queries):
        assert _queries_mentioning(captured, "tenants_gym_settings") == 1
        assert _queries_mentioning(captured, "trainers_trainer_assignment") == 1
        assert _queries_mentioning(captured, "trainers_pt_booking") == 2
        assert _queries_mentioning(captured, "users_refresh_session") == 0
        assert _queries_mentioning(captured, "tenants_role_permission") == 0
    assert preset_data["generated_at"].startswith("2026-10-20T06:00:00")
    assert [row["id"] for row in preset_data["upcoming_bookings"]] == expected_ids
    assert wide_data["active_assigned_members"] == 2
    assert wide_data["sessions"]["bookings"] == 0
    assert [row["id"] for row in wide_data["upcoming_bookings"]] == expected_ids

    later = FROZEN + timedelta(days=3)
    _freeze(monkeypatch, later)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            one_day_dashboard = trainer_dashboard(
                gym=loaded,
                trainer_staff_profile_id=ada.id,
                date_from=date(2026, 10, 18),
                date_to=date(2026, 10, 18),
                today=TODAY,
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_span:
            full_span_dashboard = trainer_dashboard(
                gym=loaded,
                trainer_staff_profile_id=ada.id,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                today=TODAY,
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as one_day_report:
            one_day_sessions = trainer_session_report(
                gym=loaded,
                trainer_staff_profile_id=ada.id,
                date_from=date(2026, 10, 18),
                date_to=date(2026, 10, 18),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_year_report:
            full_year_sessions = trainer_session_report(
                gym=loaded,
                trainer_staff_profile_id=ada.id,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    assert len(one_day.captured_queries) == TRAINER_DASHBOARD_QUERY_COUNT
    assert len(full_span.captured_queries) == TRAINER_DASHBOARD_QUERY_COUNT
    assert len(one_day_report.captured_queries) == TRAINER_SESSION_REPORT_QUERY_COUNT
    assert len(full_year_report.captured_queries) == TRAINER_SESSION_REPORT_QUERY_COUNT
    for captured in (one_day, full_span):
        assert _queries_mentioning(captured, "tenants_gym_settings") == 0
        assert _queries_mentioning(captured, "trainers_trainer_assignment") == 1
        assert _queries_mentioning(captured, "trainers_pt_booking") == 2
    for captured in (one_day_report, full_year_report):
        booking_reads = _queries_mentioning(captured, "trainers_pt_booking")
        assert booking_reads == TRAINER_SESSION_REPORT_QUERY_COUNT
        assert _queries_mentioning(captured, "trainers_trainer_assignment") == 0
    assert one_day_dashboard["generated_at"] == FROZEN
    assert full_span_dashboard["generated_at"] == FROZEN
    assert one_day_dashboard["active_assigned_members"] == 2
    assert _counts(one_day_dashboard["sessions"]) == _counts(ada_day["totals"])
    assert [str(row["id"]) for row in one_day_dashboard["upcoming_bookings"]] == expected_ids
    assert full_span_dashboard["sessions"]["bookings"] == 0
    assert [str(row["id"]) for row in full_span_dashboard["upcoming_bookings"]] == expected_ids
    assert len(full_year_sessions["rows"]) == 366
    assert full_year_sessions["totals"]["bookings"] == 0
    assert _counts(one_day_sessions["totals"]) == _counts(ada_day["totals"])
    _freeze(monkeypatch, FROZEN)

    _disable(gym, "pt")
    assert _statuses(api, ada_headers, f"{DASHBOARD}?range=today") == 403
    assert _statuses(api, ada_headers, f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18") == 403
    _enable(gym, "pt")
    _disable(gym, "reports")
    assert _statuses(api, ada_headers, f"{DASHBOARD}?range=today") == 403
    assert (
        _statuses(api, lead_headers, f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18") == 403
    )
    _enable(gym, "reports")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        removed = RolePermission.objects.filter(
            gym=gym,
            role__key="TRAINER",
            role__source_template__key="TRAINER",
            permission__code="reports.view",
        ).delete()
    assert removed[0] >= 1
    assert _statuses(api, ada_headers, f"{DASHBOARD}?range=today") == 403
    assert _statuses(api, ada_headers, f"{SESSIONS}?date_from=2026-10-18&date_to=2026-10-18") == 403
    assert _statuses(api, _headers(staff["reception"]), f"{DASHBOARD}?range=today") == 404
    still_own, still_body = _report(api, lead_headers, f"{DASHBOARD}?range=today")
    assert still_own["trainer_staff_profile_id"] == str(lead.id)
    assert still_own["active_assigned_members"] == 0
    assert still_own["upcoming_bookings"] == []
    assert nila["member_number"] not in still_body

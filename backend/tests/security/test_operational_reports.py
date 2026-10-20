"""Membership-expiry and check-in reports, isolation, and bounded reads."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import CheckIn, CheckInDevice, Membership
from apps.reports.selectors import (
    CHECK_IN_QUERY_COUNT,
    CHECK_IN_SOURCE_STATUS_POLICY,
    MEMBERSHIP_EXPIRY_QUERY_COUNT,
    MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY,
    check_in_report,
    membership_expiry_report,
)
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from tests.security.test_dashboard import (
    FROZEN,
    _headers,
    _member,
    _membership,
    _plan,
    _staff,
    _zone,
)
from tests.security.test_retail import _gym
from tests.security.test_tenant_gym_settings import jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

LA = ZoneInfo("America/Los_Angeles")


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _report(api: Any, headers: dict[str, str], path: str) -> dict[str, Any]:
    response = api.get(path, **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _row(report: dict[str, Any], day: str) -> dict[str, Any]:
    rows = report["rows"]
    assert isinstance(rows, list)
    matches = [row for row in rows if isinstance(row, dict) and row["date"] == day]
    assert len(matches) == 1
    return matches[0]


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=LA)


def _device(gym: Gym, actor: GymMembership, kind: str) -> CheckInDevice:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return CheckInDevice.objects.create(
            gym=gym,
            label="Desk",
            location="Lobby",
            kind=kind,
            status=CheckInDevice.Status.ACTIVE,
            credential_selector=f"{kind.lower()}{uuid4().hex}",
            credential_hash=make_password("not-a-device-secret", hasher="pbkdf2_sha256"),
            credential_version=1,
            created_by_membership_id=actor.id,
        )


def _visit(
    gym: Gym,
    actor: GymMembership,
    member: object,
    *,
    method: str,
    arrived: datetime,
    departed: datetime | None = None,
    device: CheckInDevice | None = None,
) -> None:
    manual = method == CheckIn.Method.MANUAL
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        CheckIn.objects.create(
            gym=gym,
            member=member,
            check_in_at=arrived,
            check_out_at=departed,
            method=method,
            device_id=None if device is None else device.id,
            recorded_by_membership_id=actor.id if manual else None,
            created_by_membership_id=actor.id if manual else None,
            eligibility_snapshot={"eligible": True},
        )


@override_settings(**jwt_settings())
def test_operational_reports_reuse_the_range_and_reports_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, _owner, _owner_headers = _gym("OPGATE")
    staff = _staff(gym)
    reception = _headers(staff["reception"])
    expiry = "/api/v1/reports/membership-expiry/?date_from=2024-02-28&date_to=2024-03-01"
    visits = "/api/v1/reports/check-ins/?date_from=2024-02-28&date_to=2024-03-01"
    assert api.get("/api/v1/reports/membership-expiry/", **reception).status_code == 400
    assert (
        api.get("/api/v1/reports/check-ins/?date_from=2026-10-01", **reception).status_code == 400
    )
    assert (
        api.get(f"{expiry}&gym_id={gym.id}&status=ACTIVE&formula=cash", **reception).status_code
        == 400
    )
    duplicate = (
        "/api/v1/reports/check-ins/?date_from=2026-10-01&date_from=2026-10-02&date_to=2026-10-02"
    )
    assert api.get(duplicate, **reception).status_code == 400
    assert (
        api.get(
            "/api/v1/reports/membership-expiry/?date_from=&date_to=2026-10-01",
            **reception,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/check-ins/?date_from=2026-10-02&date_to=2026-10-01",
            **reception,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/membership-expiry/?date_from=2024-01-01&date_to=2025-01-01",
            **reception,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/check-ins/?date_from=1899-12-31&date_to=1899-12-31",
            **reception,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/membership-expiry/?date_from=2100-01-01&date_to=2100-01-01",
            **reception,
        ).status_code
        == 400
    )
    assert api.get("/api/v1/reports/daily/?date=2026-10-01", **reception).status_code == 403
    assert api.get(expiry, **_headers(staff["trainer"])).status_code == 404
    assert api.get(visits, **_headers(staff["trainer"])).status_code == 404

    leap = _report(api, reception, expiry)
    assert "currency" not in leap
    assert leap["source_status_policy"] == MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY
    assert leap["range"] == {"date_from": "2024-02-28", "date_to": "2024-03-01"}
    assert [row["date"] for row in leap["rows"]] == ["2024-02-28", "2024-02-29", "2024-03-01"]
    assert all(row["expiring_memberships"] == 0 for row in leap["rows"])
    visit_leap = _report(api, reception, visits)
    assert "currency" not in visit_leap
    assert visit_leap["source_status_policy"] == CHECK_IN_SOURCE_STATUS_POLICY
    assert [row["date"] for row in visit_leap["rows"]] == [
        "2024-02-28",
        "2024-02-29",
        "2024-03-01",
    ]
    assert all(
        row["check_ins"] == 0 and row["open_visits_from_day"] == 0 for row in visit_leap["rows"]
    )
    full_year = _report(
        api,
        reception,
        "/api/v1/reports/check-ins/?date_from=2024-01-01&date_to=2024-12-31",
    )
    assert len(full_year["rows"]) == 366
    assert full_year["rows"][0]["date"] == "2024-01-01"
    assert full_year["rows"][-1]["date"] == "2024-12-31"
    expiry_year = _report(
        api,
        reception,
        "/api/v1/reports/membership-expiry/?date_from=2024-01-01&date_to=2024-12-31",
    )
    assert len(expiry_year["rows"]) == 366
    GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(enabled=False)
    assert api.get(expiry, **reception).status_code == 403
    assert api.get(visits, **reception).status_code == 403


@override_settings(**jwt_settings())
def test_operational_reports_follow_current_status_and_gym_local_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    before_utc_date = _local(2026, 3, 7, 23, 30)
    after_spring = _local(2026, 3, 8, 3, 30)
    utc_next_day = _local(2026, 3, 8, 17, 0)
    assert before_utc_date.astimezone(UTC).date() == date(2026, 3, 8)
    assert utc_next_day.astimezone(UTC).date() == date(2026, 3, 9)
    assert before_utc_date.utcoffset() != after_spring.utcoffset()

    api, gym, owner, _owner_headers = _gym("OPZONE")
    _other_api, other, other_owner, other_headers = _gym("OPOTHR")
    _zone(gym, "America/Los_Angeles", 1)
    _zone(other, "America/Los_Angeles", 1)
    staff = _staff(gym)
    plan = _plan(gym, owner)
    other_plan = _plan(other, other_owner)
    ada = _member(gym, owner, number="OP-A", name="Ada", joined=date(2026, 1, 1))
    bea = _member(gym, owner, number="OP-B", name="Bea", joined=date(2026, 1, 1))
    cy = _member(gym, owner, number="OP-C", name="Cy", joined=date(2026, 1, 1))
    dee = _member(gym, owner, number="OP-D", name="Dee", joined=date(2026, 1, 1))
    _membership(
        gym,
        owner,
        member=ada,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        status=Membership.Status.ACTIVE,
    )
    _membership(
        gym,
        owner,
        member=ada,
        plan=plan,
        start=date(2026, 8, 1),
        expiry=date(2026, 10, 1),
        amount="10.00",
        source=Membership.RevenueSource.RENEWAL,
        status=Membership.Status.EXPIRED,
    )
    _membership(
        gym,
        owner,
        member=ada,
        plan=plan,
        start=date(2026, 7, 1),
        expiry=date(2026, 10, 3),
        amount="10.00",
        source=Membership.RevenueSource.RENEWAL,
        status=Membership.Status.EXPIRED,
    )
    _membership(
        gym,
        owner,
        member=bea,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        status=Membership.Status.SUSPENDED,
    )
    _membership(
        gym,
        owner,
        member=cy,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        status=Membership.Status.PENDING,
    )
    _membership(
        gym,
        owner,
        member=dee,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        status=Membership.Status.CANCELLED,
    )
    outsider = _member(other, other_owner, number="OP-Z", name="Zoe", joined=date(2026, 1, 1))
    _membership(
        other,
        other_owner,
        member=outsider,
        plan=other_plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="99.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )

    ann = _member(gym, owner, number="OP-1", name="Ann", joined=date(2026, 1, 1))
    cam = _member(gym, owner, number="OP-2", name="Cam", joined=date(2026, 1, 1))
    bo = _member(gym, owner, number="OP-3", name="Bo", joined=date(2026, 1, 1))
    nia = _member(gym, owner, number="OP-4", name="Nia", joined=date(2026, 1, 1))
    ed = _member(gym, owner, number="OP-5", name="Ed", joined=date(2026, 1, 1))
    nfc = _device(gym, owner, CheckInDevice.Kind.NFC)
    biometric = _device(gym, owner, CheckInDevice.Kind.BIOMETRIC)
    _visit(
        gym,
        owner,
        ann,
        method=CheckIn.Method.MANUAL,
        arrived=before_utc_date,
    )
    _visit(
        gym,
        owner,
        cam,
        method=CheckIn.Method.MANUAL,
        arrived=_local(2026, 3, 7, 22, 0),
        departed=after_spring,
    )
    _visit(
        gym,
        owner,
        bo,
        method=CheckIn.Method.QR,
        arrived=_local(2026, 3, 8, 1, 30),
        departed=_local(2026, 3, 8, 4, 0),
    )
    _visit(
        gym,
        owner,
        nia,
        method=CheckIn.Method.NFC,
        arrived=after_spring,
        device=nfc,
    )
    _visit(
        gym,
        owner,
        ed,
        method=CheckIn.Method.BIOMETRIC,
        arrived=utc_next_day,
        departed=_local(2026, 3, 9, 1, 0),
        device=biometric,
    )
    _visit(
        other,
        other_owner,
        outsider,
        method=CheckIn.Method.MANUAL,
        arrived=_local(2026, 3, 8, 12, 0),
    )

    reception = _headers(staff["reception"])
    expiry = _report(
        api,
        reception,
        "/api/v1/reports/membership-expiry/?date_from=2026-10-01&date_to=2026-10-03",
    )
    assert expiry["timezone"] == "America/Los_Angeles"
    assert "currency" not in expiry
    october_first = _row(expiry, "2026-10-01")
    assert october_first["expiring_memberships"] == 3
    assert october_first["unique_members"] == 2
    assert october_first["active_memberships"] == 1
    assert october_first["suspended_memberships"] == 1
    assert october_first["expired_memberships"] == 1
    assert _row(expiry, "2026-10-02")["expiring_memberships"] == 0
    october_third = _row(expiry, "2026-10-03")
    assert october_third["expiring_memberships"] == 1
    assert october_third["unique_members"] == 1
    assert october_third["expired_memberships"] == 1
    assert expiry["totals"]["expiring_memberships"] == 4
    assert expiry["totals"]["unique_members"] == 2
    assert expiry["totals"]["active_memberships"] == 1
    assert expiry["totals"]["suspended_memberships"] == 1
    assert expiry["totals"]["expired_memberships"] == 2
    daily_people = sum(row["unique_members"] for row in expiry["rows"])
    assert daily_people == 3
    assert expiry["totals"]["unique_members"] != daily_people

    visits = _report(
        api,
        reception,
        "/api/v1/reports/check-ins/?date_from=2026-03-08&date_to=2026-03-09",
    )
    assert visits["timezone"] == "America/Los_Angeles"
    march_eighth = _row(visits, "2026-03-08")
    assert march_eighth["check_ins"] == 3
    assert march_eighth["unique_attendees"] == 3
    assert march_eighth["manual_check_ins"] == 0
    assert march_eighth["qr_check_ins"] == 1
    assert march_eighth["nfc_check_ins"] == 1
    assert march_eighth["biometric_check_ins"] == 1
    assert march_eighth["check_outs"] == 2
    assert march_eighth["open_visits_from_day"] == 1
    march_ninth = _row(visits, "2026-03-09")
    assert march_ninth["check_ins"] == 0
    assert march_ninth["unique_attendees"] == 0
    assert march_ninth["check_outs"] == 1
    assert march_ninth["open_visits_from_day"] == 0
    assert visits["totals"]["check_ins"] == 3
    assert visits["totals"]["unique_attendees"] == 3
    assert visits["totals"]["check_outs"] == 3
    assert visits["totals"]["open_visits_from_day"] == 1
    assert visits["totals"]["currently_checked_in"] == 2
    assert visits["totals"]["manual_check_ins"] == 0
    assert visits["totals"]["qr_check_ins"] == 1
    assert visits["totals"]["nfc_check_ins"] == 1
    assert visits["totals"]["biometric_check_ins"] == 1
    for row in visits["rows"]:
        assert row["check_ins"] == (
            row["manual_check_ins"]
            + row["qr_check_ins"]
            + row["nfc_check_ins"]
            + row["biometric_check_ins"]
        )
    later = _report(
        api,
        reception,
        "/api/v1/reports/check-ins/?date_from=2026-03-09&date_to=2026-03-09",
    )
    assert _row(later, "2026-03-09")["open_visits_from_day"] == 0
    assert _row(later, "2026-03-09")["check_ins"] == 0
    assert later["totals"]["currently_checked_in"] == 2

    isolated = _report(
        _other_api,
        other_headers,
        "/api/v1/reports/membership-expiry/?date_from=2026-10-01&date_to=2026-10-01",
    )
    assert isolated["totals"]["expiring_memberships"] == 1
    assert isolated["totals"]["unique_members"] == 1
    other_visits = _report(
        _other_api,
        other_headers,
        "/api/v1/reports/check-ins/?date_from=2026-03-08&date_to=2026-03-08",
    )
    assert other_visits["totals"]["check_ins"] == 1
    assert other_visits["totals"]["currently_checked_in"] == 1

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as expiry_day:
            membership_expiry_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as expiry_year:
            membership_expiry_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as visit_day:
            check_in_report(
                gym=loaded,
                date_from=date(2026, 3, 8),
                date_to=date(2026, 3, 8),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as visit_year:
            check_in_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    assert len(expiry_day.captured_queries) == MEMBERSHIP_EXPIRY_QUERY_COUNT
    assert len(expiry_year.captured_queries) == MEMBERSHIP_EXPIRY_QUERY_COUNT
    assert len(visit_day.captured_queries) == CHECK_IN_QUERY_COUNT
    assert len(visit_year.captured_queries) == CHECK_IN_QUERY_COUNT

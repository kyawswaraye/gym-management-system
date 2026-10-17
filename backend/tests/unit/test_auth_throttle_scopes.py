from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import AnonRateThrottle

from apps.users.throttling import (
    DeviceChallengeIPThrottle,
    DeviceChallengeSelectorThrottle,
    DeviceIngestIPThrottle,
    DeviceIngestSelectorThrottle,
    MemberCheckInScanThrottle,
    MemberLoginIPThrottle,
    MemberLoginThrottle,
    MemberLogoutIPThrottle,
    MemberLogoutTokenThrottle,
    MemberRefreshIPThrottle,
    MemberRefreshTokenThrottle,
)

_BOUNDARY_THROTTLES = (
    DeviceChallengeIPThrottle,
    DeviceChallengeSelectorThrottle,
    DeviceIngestIPThrottle,
    DeviceIngestSelectorThrottle,
    MemberCheckInScanThrottle,
    MemberLoginIPThrottle,
    MemberLoginThrottle,
    MemberLogoutIPThrottle,
    MemberLogoutTokenThrottle,
    MemberRefreshIPThrottle,
    MemberRefreshTokenThrottle,
)


def _request(
    *,
    data: dict[str, str] | None = None,
    authorization: str = "",
    user: object | None = None,
) -> Request:
    raw = APIRequestFactory().post(
        "/api/v1/auth/login/",
        data or {},
        format="json",
        HTTP_AUTHORIZATION=authorization,
        REMOTE_ADDR="203.0.113.10",
    )
    request = Request(raw, parsers=[JSONParser()])
    if user is not None:
        request.user = user
    return request


@override_settings(REFRESH_TOKEN_PEPPER="throttle-scope-pepper")
def test_security_boundaries_do_not_share_throttle_scopes_or_keys() -> None:
    credential = "gms-device:v1:selector-value-0001.secret-value"
    refresh_token = "member-refresh-token-not-a-cache-key"
    request = _request(
        data={"email": "Member@Example.test", "refresh_token": refresh_token},
        authorization=f"Device {credential}",
    )
    other_email = _request(data={"email": "other@example.test", "refresh_token": refresh_token})

    class _Member:
        pk = uuid4()
        is_authenticated = True

    scan = _request(user=_Member())
    keys = {
        throttle.__name__: throttle().get_cache_key(request, None)
        for throttle in _BOUNDARY_THROTTLES
        if throttle is not MemberCheckInScanThrottle
    }
    keys[MemberCheckInScanThrottle.__name__] = MemberCheckInScanThrottle().get_cache_key(scan, None)
    anon_key = AnonRateThrottle().get_cache_key(request, None)

    assert anon_key is not None
    assert len(keys) == len(set(keys.values()))
    assert all(key is not None and key != anon_key for key in keys.values())
    assert {throttle.scope for throttle in _BOUNDARY_THROTTLES}.isdisjoint({"anon", "user"})
    assert not issubclass(DeviceIngestIPThrottle, DeviceChallengeIPThrottle)
    assert keys["DeviceChallengeIPThrottle"] != keys["DeviceIngestIPThrottle"]
    assert DeviceChallengeSelectorThrottle().get_cache_key(
        request, None
    ) != DeviceIngestSelectorThrottle().get_cache_key(request, None)
    login = MemberLoginThrottle()
    assert login.get_cache_key(request, None) != login.get_cache_key(other_email, None)
    rendered = " ".join(str(key) for key in keys.values())
    assert refresh_token not in rendered
    assert "secret-value" not in rendered
    assert "Member@Example.test" not in rendered


@override_settings(REFRESH_TOKEN_PEPPER="throttle-scope-pepper")
def test_member_login_exhaustion_does_not_consume_the_staff_anon_bucket() -> None:
    member_limit = int(
        str(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["member_login_ip"]).split("/")[0]
    )
    anon_limit = int(str(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"]).split("/")[0])
    cache.clear()
    try:
        member = _request(data={"email": "member@example.test"})
        staff = _request(data={"email": "staff@example.test"})
        for _attempt in range(member_limit):
            assert MemberLoginIPThrottle().allow_request(member, None) is True
        assert MemberLoginIPThrottle().allow_request(member, None) is False
        assert AnonRateThrottle().allow_request(staff, None) is True

        cache.clear()
        for _attempt in range(anon_limit):
            assert AnonRateThrottle().allow_request(staff, None) is True
        assert AnonRateThrottle().allow_request(staff, None) is False
        assert MemberLoginIPThrottle().allow_request(member, None) is True
        assert MemberRefreshIPThrottle().allow_request(member, None) is True
        assert DeviceChallengeIPThrottle().allow_request(member, None) is True
        assert DeviceIngestIPThrottle().allow_request(member, None) is True
    finally:
        cache.clear()

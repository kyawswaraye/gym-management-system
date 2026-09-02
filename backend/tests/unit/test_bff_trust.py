from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.common.api.bff import BffTrustMiddleware

SECRET = "test-bff-shared-secret-32-chars-min"


def _middleware() -> BffTrustMiddleware:
    return BffTrustMiddleware(lambda request: HttpResponse("ok"))


def test_health_does_not_require_bff_secret() -> None:
    request = RequestFactory().get("/api/v1/health/")
    response = _middleware()(request)
    assert response.status_code == 200


@override_settings(BFF_SHARED_SECRET=SECRET)
def test_auth_without_bff_secret_is_rejected() -> None:
    request = RequestFactory().post("/api/v1/auth/login/")
    response = _middleware()(request)
    assert response.status_code == 403
    assert b"BFF_REQUIRED" in response.content


@override_settings(BFF_SHARED_SECRET=SECRET)
def test_auth_with_trusted_bff_secret_is_accepted() -> None:
    request = RequestFactory().post("/api/v1/auth/login/", HTTP_X_GYM_BFF_SECRET=SECRET)
    response = _middleware()(request)
    assert response.status_code == 200


@override_settings(BFF_SHARED_SECRET="")
def test_missing_configured_secret_fails_closed() -> None:
    request = RequestFactory().post("/api/v1/auth/select-gym/", HTTP_X_GYM_BFF_SECRET=SECRET)
    response = _middleware()(request)
    assert response.status_code == 403

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.test import APIRequestFactory

from apps.common.api.middleware import RequestIDMiddleware
from apps.common.api.views import HealthView
from apps.users.serializers import LoginInputSerializer, SelectGymInputSerializer


def test_health_envelope_and_request_id() -> None:
    request = APIRequestFactory().get("/api/v1/health/")
    response = HealthView.as_view()(request)
    response.render()
    assert response.status_code == 200
    assert response.data == {
        "success": True,
        "data": {"status": "ok"},
        "message": "Success",
    }


def test_invalid_request_id_is_replaced() -> None:
    middleware = RequestIDMiddleware(lambda request: HttpResponse())
    request = APIRequestFactory().get("/api/v1/health/", HTTP_X_REQUEST_ID="bad request id")
    response = middleware(request)
    assert response["X-Request-ID"].startswith("req_")


def test_login_rejects_mass_assignment() -> None:
    serializer = LoginInputSerializer(
        data={
            "email": "owner@example.test",
            "password": "correct horse battery staple",
            "gym_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "is_owner": True,
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"gym_id", "is_owner"}


def test_select_gym_accepts_only_membership_id() -> None:
    serializer = SelectGymInputSerializer(
        data={
            "gym_membership_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "gym_id": "c131004c-1948-4717-b310-c0afc28bb36c",
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"gym_id"}


def test_select_gym_allows_ticket_for_bff() -> None:
    serializer = SelectGymInputSerializer(
        data={
            "gym_membership_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "gym_selection_ticket": "opaque-ticket",
        }
    )
    assert serializer.is_valid()

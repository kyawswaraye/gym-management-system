"""Staff command dispatch must not invent an HTTP method."""

from __future__ import annotations

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from apps.assistant.views import AssistantTurnView


@pytest.mark.django_db
def test_missing_http_method_does_not_run_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    django_request = APIRequestFactory().post("/api/v1/assistant/turns/")
    django_request.method = None
    called: list[str] = []

    def initial(self: AssistantTurnView, request: object, *args: object, **kwargs: object) -> None:
        called.append("initial")

    def post(self: AssistantTurnView, request: object) -> Response:
        called.append("post")
        return Response({})

    monkeypatch.setattr(AssistantTurnView, "initial", initial)
    monkeypatch.setattr(AssistantTurnView, "post", post)
    response = AssistantTurnView().dispatch(django_request)

    assert called == ["initial"]
    assert response.status_code == 405


@pytest.mark.django_db
def test_post_still_reaches_the_turn_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    django_request = APIRequestFactory().post("/api/v1/assistant/turns/")
    seen: list[object] = []

    def initial(self: AssistantTurnView, request: object, *args: object, **kwargs: object) -> None:
        return None

    def post(self: AssistantTurnView, request: object) -> Response:
        seen.append(getattr(request, "method", None))
        return Response({"ok": True})

    monkeypatch.setattr(AssistantTurnView, "initial", initial)
    monkeypatch.setattr(AssistantTurnView, "post", post)
    response = AssistantTurnView().dispatch(django_request)

    assert seen == ["POST"]
    assert response.status_code == 200

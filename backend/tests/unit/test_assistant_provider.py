from __future__ import annotations

import logging
import urllib.error
from email.message import EmailMessage
from io import BytesIO

import pytest
from django.test import override_settings

from apps.assistant import provider as provider_module
from apps.assistant.configuration import ProviderFailure
from apps.assistant.dispatch import tool_contracts
from apps.assistant.e2e_provider import scripted_response
from apps.assistant.executor import ApprovalSnapshot
from apps.assistant.proposals import declaration, model_arguments, with_snapshot
from apps.assistant.provider import ProviderCallError, generate_content, parse_model_turn

_SETTINGS = {
    "ASSISTANT_ENABLED": "true",
    "GEMINI_API_KEY": "k" * 20,
    "GEMINI_MODEL": "gemini-test",
    "GEMINI_MAX_RETRIES": "1",
}


def test_model_turn_rejects_a_malformed_function_call() -> None:
    with pytest.raises(ProviderCallError):
        parse_model_turn(
            {
                "candidates": [
                    {"content": {"parts": [{"functionCall": {"name": "Search", "args": {}}}]}}
                ]
            }
        )
    with pytest.raises(ProviderCallError):
        parse_model_turn(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "search_payments", "args": []}}]
                        }
                    }
                ]
            }
        )


def test_blocked_prompt_has_no_tool_call() -> None:
    turn = parse_model_turn({"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})
    assert turn.blocked is True
    assert turn.calls == ()


def test_generate_content_does_not_retry_a_timeout_or_reveal_the_key() -> None:
    seen: list[dict[str, object]] = []

    def transport(body: dict[str, object], timeout: int) -> dict[str, object]:
        assert timeout == 30
        seen.append(body)
        raise ProviderCallError(ProviderFailure.TIMEOUT)

    with override_settings(**_SETTINGS):
        with pytest.raises(ProviderCallError) as raised:
            generate_content(
                contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
                declarations=[],
                system_instruction="Stay in the gym.",
                transport=transport,
            )
    assert raised.value.failure == ProviderFailure.TIMEOUT
    assert raised.value.reached_provider is True
    assert len(seen) == 1
    assert "k" * 20 not in str(raised.value)


def test_credential_failure_is_not_retried() -> None:
    calls = {"count": 0}

    def transport(_body: dict[str, object], _timeout: int) -> dict[str, object]:
        calls["count"] += 1
        raise ProviderCallError(ProviderFailure.CREDENTIAL)

    with override_settings(**_SETTINGS):
        try:
            generate_content(
                contents=[],
                declarations=[],
                system_instruction="Stay in the gym.",
                transport=transport,
            )
        except ProviderCallError as exc:
            assert exc.failure == ProviderFailure.CREDENTIAL
            assert "k" * 20 not in str(exc)
        else:
            raise AssertionError("expected provider failure")
    assert calls["count"] == 1


def test_model_arguments_cannot_confirm_or_choose_an_amount() -> None:
    payload = model_arguments(
        {
            "payment_id": "pay-1",
            "confirmed": True,
            "gym_id": "gym-1",
            "record_id": "model-record",
            "amount": "1.00",
        },
        approval=True,
    )
    assert "confirmed" not in payload
    assert "gym_id" not in payload
    assert "amount" not in payload
    assert "record_id" not in payload
    stored = with_snapshot(payload, ApprovalSnapshot(record_id="pay-1", amount="40.00"))
    assert stored["amount"] == "40.00"
    assert stored["record_id"] == "pay-1"
    assert "confirmed" not in stored


def test_approval_declaration_omits_the_stored_amount() -> None:
    contract = tool_contracts()["approve_payment"]
    schema = declaration(contract)
    properties = schema["parameters"]["properties"]
    assert isinstance(properties, dict)
    assert "amount" not in properties
    assert "record_id" not in properties
    assert "payment_id" in properties
    assert "confirmed" not in properties


@override_settings(
    ASSISTANT_ENABLED="true",
    ASSISTANT_E2E_SCRIPT=True,
    SETTINGS_MODULE="config.settings.e2e",
    GEMINI_API_KEY="",
    GEMINI_MODEL="",
)
def test_e2e_script_calls_the_read_without_gemini() -> None:
    turn = generate_content(
        contents=[{"role": "user", "parts": [{"text": "Search members."}]}],
        declarations=[],
        system_instruction="test",
    )

    assert [call.name for call in turn.calls] == ["search_members"]
    assert turn.text == ""


@override_settings(
    ASSISTANT_ENABLED="true",
    ASSISTANT_E2E_SCRIPT=True,
    SETTINGS_MODULE="config.settings.local",
    GEMINI_API_KEY="",
    GEMINI_MODEL="",
)
def test_e2e_script_stays_off_outside_the_e2e_settings() -> None:
    with pytest.raises(ProviderCallError) as raised:
        generate_content(
            contents=[{"role": "user", "parts": [{"text": "Search members."}]}],
            declarations=[],
            system_instruction="test",
        )

    assert raised.value.failure == ProviderFailure.CREDENTIAL


def test_e2e_script_creates_a_plan_instead_of_a_member() -> None:
    payload = scripted_response(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Create a membership plan code=E2EREJECT name=RejectMe "
                                "duration_value=30 duration_unit=DAY price=7.00"
                            )
                        }
                    ],
                }
            ]
        }
    )

    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    content = candidate["content"]
    assert isinstance(content, dict)
    parts = content["parts"]
    assert isinstance(parts, list)
    part = parts[0]
    assert isinstance(part, dict)
    call = part["functionCall"]
    assert isinstance(call, dict)
    assert call["name"] == "create_plan"
    arguments = call["args"]
    assert isinstance(arguments, dict)
    assert arguments["code"] == "E2EREJECT"
    assert arguments["price"] == "7.00"


def test_e2e_script_stops_after_the_plan_proposal() -> None:
    payload = scripted_response(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Create a membership plan code=E2EONCE name=Once price=8.00"}
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "create_plan",
                                "response": {"status": "confirmation_required"},
                            }
                        }
                    ],
                },
            ]
        }
    )

    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    content = candidate["content"]
    assert isinstance(content, dict)
    parts = content["parts"]
    assert isinstance(parts, list)
    part = parts[0]
    assert isinstance(part, dict)
    assert part["text"] == "Review the change before confirming."


def test_e2e_script_reports_completion_only_after_the_server_saved_it() -> None:
    payload = scripted_response(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "The change is saved. Continue only if a different "
                                "step is still required."
                            )
                        }
                    ],
                }
            ]
        }
    )

    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    content = candidate["content"]
    assert isinstance(content, dict)
    parts = content["parts"]
    assert isinstance(parts, list)
    part = parts[0]
    assert isinstance(part, dict)
    assert part["text"] == "The change is complete."


class _Body:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def read(self, _limit: int) -> bytes:
        return self.raw

    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def _http_error(status: int, payload: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com",
        status,
        "error",
        EmailMessage(),
        BytesIO(payload),
    )


def test_only_an_unsent_connection_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    pauses: list[int] = []
    calls = {"count": 0}

    def urlopen(_request: object, timeout: int) -> _Body:
        assert timeout == 30
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        return _Body(
            b'{"candidates":[{"content":{"parts":[{"text":"Hello"}]}}],'
            b'"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":1,"totalTokenCount":9}}'
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(provider_module, "_pause_before_retry", lambda: pauses.append(1))
    with override_settings(**{**_SETTINGS, "GEMINI_MAX_RETRIES": "5"}):
        turn = generate_content(
            contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
            declarations=[],
            system_instruction="salary 900000",
            transport=None,
        )
    assert turn.text == "Hello"
    assert turn.usage.prompt_tokens == 8
    assert calls["count"] == 2
    assert pauses == [1]


def test_timeout_quota_and_reset_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    calls = {"count": 0}

    def refuse_timeout(_request: object, timeout: int) -> _Body:
        del timeout
        calls["count"] += 1
        raise TimeoutError()

    monkeypatch.setattr(urllib.request, "urlopen", refuse_timeout)
    with override_settings(**{**_SETTINGS, "GEMINI_MAX_RETRIES": "5"}):
        with pytest.raises(ProviderCallError) as timed_out:
            generate_content(
                contents=[],
                declarations=[],
                system_instruction="test",
            )
    assert timed_out.value.failure == ProviderFailure.TIMEOUT
    assert timed_out.value.reached_provider is True
    assert calls["count"] == 1

    def refuse_reset(_request: object, timeout: int) -> _Body:
        del timeout
        calls["count"] += 1
        raise urllib.error.URLError(ConnectionResetError(54, "reset"))

    calls["count"] = 0
    monkeypatch.setattr(urllib.request, "urlopen", refuse_reset)
    with override_settings(**{**_SETTINGS, "GEMINI_MAX_RETRIES": "5"}):
        with pytest.raises(ProviderCallError) as reset:
            generate_content(contents=[], declarations=[], system_instruction="test")
    assert reset.value.reached_provider is True
    assert reset.value.failure == ProviderFailure.NETWORK
    assert calls["count"] == 1

    def refuse_quota(_request: object, timeout: int) -> _Body:
        del timeout
        calls["count"] += 1
        raise _http_error(
            429,
            b'{"error":{"status":"RESOURCE_EXHAUSTED","message":"MEM-9 salary"}}',
        )

    calls["count"] = 0
    monkeypatch.setattr(urllib.request, "urlopen", refuse_quota)
    with override_settings(**_SETTINGS):
        with pytest.raises(ProviderCallError) as quota:
            generate_content(contents=[], declarations=[], system_instruction="test")
    assert quota.value.failure == ProviderFailure.QUOTA
    assert calls["count"] == 1
    assert "MEM-9" not in str(quota.value)
    assert "salary" not in str(quota.value)


def test_retry_pause_is_short(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(provider_module.time, "sleep", slept.append)
    provider_module._pause_before_retry()
    assert slept == [0.2]


def test_usage_metadata_is_logged_without_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del monkeypatch
    secret = "salary 900000 MEM-000101"
    caplog.set_level(logging.INFO, logger="apps.assistant.provider")

    def transport(_body: dict[str, object], _timeout: int) -> dict[str, object]:
        return {"candidates": [{"content": {"parts": [{"text": secret}]}}]}

    with override_settings(**_SETTINGS):
        turn = generate_content(
            contents=[{"role": "user", "parts": [{"text": secret}]}],
            declarations=[],
            system_instruction=secret,
            transport=transport,
        )
    assert turn.usage.prompt_tokens is None
    assert "prompt_tokens=unavailable" in caplog.text
    assert "candidate_tokens=unavailable" in caplog.text
    assert "total_tokens=unavailable" in caplog.text
    assert "outcome=ok" in caplog.text
    assert "requests=1" in caplog.text
    assert secret not in caplog.text
    assert "k" * 20 not in caplog.text


def test_present_usage_counts_are_logged_and_booleans_are_not_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apps.assistant.provider")
    parsed = parse_model_turn(
        {
            "candidates": [{"content": {"parts": [{"text": "Hi"}]}}],
            "usageMetadata": {
                "promptTokenCount": True,
                "candidatesTokenCount": -3,
                "totalTokenCount": "12",
            },
        }
    )
    assert parsed.usage.prompt_tokens is None
    assert parsed.usage.candidate_tokens is None
    assert parsed.usage.total_tokens is None

    def transport(_body: dict[str, object], _timeout: int) -> dict[str, object]:
        return {
            "candidates": [{"content": {"parts": [{"text": "Hi"}]}}],
            "usageMetadata": {
                "promptTokenCount": 15,
                "candidatesTokenCount": 2,
                "totalTokenCount": 17,
            },
        }

    with override_settings(**_SETTINGS):
        turn = generate_content(
            contents=[],
            declarations=[],
            system_instruction="hidden prompt",
            transport=transport,
        )
    assert turn.usage.prompt_tokens == 15
    assert turn.usage.candidate_tokens == 2
    assert turn.usage.total_tokens == 17
    assert "prompt_tokens=15" in caplog.text
    assert "candidate_tokens=2" in caplog.text
    assert "total_tokens=17" in caplog.text
    assert "hidden prompt" not in caplog.text

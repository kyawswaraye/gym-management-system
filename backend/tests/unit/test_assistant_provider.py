from __future__ import annotations

import pytest
from django.test import override_settings

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


def test_generate_content_retries_transport_failures_and_hides_the_key() -> None:
    seen: list[dict[str, object]] = []

    def transport(body: dict[str, object], timeout: int) -> dict[str, object]:
        assert timeout == 30
        seen.append(body)
        if len(seen) == 1:
            raise ProviderCallError(ProviderFailure.TIMEOUT)
        return {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}

    with override_settings(**_SETTINGS):
        turn = generate_content(
            contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
            declarations=[],
            system_instruction="Stay in the gym.",
            transport=transport,
        )
    assert turn.text == "Hello"
    assert len(seen) == 2
    assert "k" * 20 not in str(seen)


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

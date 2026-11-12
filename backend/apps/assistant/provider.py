"""Server-side Gemini calls. The key stays in process settings."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

from django.conf import settings

from apps.assistant.configuration import (
    ProviderFailure,
    assistant_configuration,
    classify_provider_failure,
    e2e_script_enabled,
    gemini_max_retries,
    gemini_timeout_seconds,
)

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESPONSE_LIMIT = 1_000_000
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

Transport = Callable[[dict[str, object], int], dict[str, object]]


class ProviderCallError(Exception):
    """A provider failure whose message is the failure class, never a secret."""

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelTurn:
    text: str
    calls: tuple[FunctionCall, ...]
    parts: tuple[dict[str, object], ...]
    blocked: bool = False


def parse_model_turn(payload: Mapping[str, object]) -> ModelTurn:
    """Validate one generateContent body before any tool runs."""

    feedback = payload.get("promptFeedback")
    blocked = isinstance(feedback, Mapping) and bool(feedback.get("blockReason"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        if blocked:
            return ModelTurn("", (), (), blocked=True)
        raise ProviderCallError(ProviderFailure.PROVIDER)
    candidate = candidates[0]
    if not isinstance(candidate, Mapping):
        raise ProviderCallError(ProviderFailure.PROVIDER)
    content = candidate.get("content")
    if not isinstance(content, Mapping):
        raise ProviderCallError(ProviderFailure.PROVIDER)
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ProviderCallError(ProviderFailure.PROVIDER)
    texts: list[str] = []
    calls: list[FunctionCall] = []
    echoed: list[dict[str, object]] = []
    for part in parts:
        if not isinstance(part, Mapping):
            raise ProviderCallError(ProviderFailure.PROVIDER)
        echoed.append(dict(part))
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
        call = part.get("functionCall")
        if call is None:
            continue
        if not isinstance(call, Mapping):
            raise ProviderCallError(ProviderFailure.PROVIDER)
        name = call.get("name")
        arguments = call.get("args", {})
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise ProviderCallError(ProviderFailure.PROVIDER)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ProviderCallError(ProviderFailure.PROVIDER)
        calls.append(FunctionCall(name, cast(dict[str, object], arguments)))
    text = "".join(texts).strip()
    if text == "" and not calls and not blocked:
        raise ProviderCallError(ProviderFailure.PROVIDER)
    return ModelTurn(text, tuple(calls), tuple(echoed), blocked=blocked)


def generate_content(
    *,
    contents: list[dict[str, object]],
    declarations: list[dict[str, object]],
    system_instruction: str,
    transport: Transport | None = None,
) -> ModelTurn:
    """Call Gemini outside a database transaction. Retries cover transport only."""

    if not assistant_configuration().available:
        raise ProviderCallError(ProviderFailure.CREDENTIAL)
    body: dict[str, object] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.2},
    }
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]
    sender = transport or (_scripted_transport if e2e_script_enabled() else _post)
    attempts = gemini_max_retries()
    last = ProviderFailure.PROVIDER
    for attempt in range(attempts + 1):
        try:
            payload = sender(body, gemini_timeout_seconds())
        except ProviderCallError as exc:
            last = exc.failure
            if last not in {ProviderFailure.TIMEOUT, ProviderFailure.NETWORK}:
                raise
            if attempt >= attempts:
                raise
            continue
        try:
            return parse_model_turn(payload)
        except ProviderCallError:
            raise
        except (TypeError, ValueError) as exc:
            raise ProviderCallError(ProviderFailure.PROVIDER) from exc
    raise ProviderCallError(last)


def _scripted_transport(body: dict[str, object], timeout: int) -> dict[str, object]:
    """Playwright script. Production never selects this transport."""

    del timeout
    from apps.assistant.e2e_provider import scripted_response

    return scripted_response(body)


def _post(body: dict[str, object], timeout: int) -> dict[str, object]:
    model = str(getattr(settings, "GEMINI_MODEL", ""))
    key = str(getattr(settings, "GEMINI_API_KEY", ""))
    url = _ENDPOINT.format(model=quote(model, safe=""))
    raw = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            downloaded = response.read(_RESPONSE_LIMIT + 1)
    except TimeoutError as exc:
        raise ProviderCallError(ProviderFailure.TIMEOUT) from exc
    except urllib.error.HTTPError as exc:
        failure = _http_failure(exc)
        logger.warning("assistant provider status %s", failure.value)
        raise ProviderCallError(failure) from exc
    except urllib.error.URLError as exc:
        raise ProviderCallError(ProviderFailure.NETWORK) from exc
    if len(downloaded) > _RESPONSE_LIMIT:
        raise ProviderCallError(ProviderFailure.PROVIDER)
    try:
        parsed = json.loads(downloaded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderCallError(ProviderFailure.PROVIDER) from exc
    if not isinstance(parsed, dict):
        raise ProviderCallError(ProviderFailure.PROVIDER)
    return cast(dict[str, object], parsed)


def _http_failure(exc: urllib.error.HTTPError) -> ProviderFailure:
    status_name = ""
    try:
        downloaded = exc.read(4000)
        parsed = json.loads(downloaded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, OSError):
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("status"), str):
            status_name = error["status"]
    return classify_provider_failure(status_code=exc.code, provider_status=status_name)

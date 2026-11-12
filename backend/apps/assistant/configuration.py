from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings

_KEY_MIN = 20
_KEY_MAX = 4096
_MODEL_MAX = 128
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ProviderFailure(StrEnum):
    CREDENTIAL = "credential"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    NETWORK = "network"
    PROVIDER = "provider"


@dataclass(frozen=True)
class AssistantConfiguration:
    available: bool
    reasons: tuple[str, ...]


def _unsafe(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    )


def _flag(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE or normalized == "":
        return False
    return None


def _positive_int(value: str, default: int, upper: int) -> int:
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    if parsed < 1 or parsed > upper:
        return default
    return parsed


def e2e_script_enabled() -> bool:
    """True only for the disposable Playwright settings module."""

    if not bool(getattr(settings, "ASSISTANT_E2E_SCRIPT", False)):
        return False
    return str(getattr(settings, "SETTINGS_MODULE", "")) == "config.settings.e2e"


def assistant_configuration() -> AssistantConfiguration:
    """Read Gemini settings. This never raises and never returns the key."""

    reasons: list[str] = []
    enabled = _flag(str(getattr(settings, "ASSISTANT_ENABLED", "false")))
    if enabled is None:
        reasons.append("invalid_flag")
        enabled = False
    elif not enabled:
        reasons.append("disabled")

    key = str(getattr(settings, "GEMINI_API_KEY", ""))
    if key == "" or key.strip() == "":
        reasons.append("missing_key")
    elif not _KEY_MIN <= len(key) <= _KEY_MAX or _unsafe(key):
        reasons.append("invalid_key")

    model = str(getattr(settings, "GEMINI_MODEL", ""))
    if model == "" or model.strip() == "":
        reasons.append("missing_model")
    elif len(model) > _MODEL_MAX or _unsafe(model):
        reasons.append("invalid_model")

    if e2e_script_enabled():
        available = enabled is True
        return AssistantConfiguration(available=available, reasons=tuple(reasons))
    blocking = {"invalid_flag", "missing_key", "invalid_key", "missing_model", "invalid_model"}
    available = enabled is True and not any(reason in blocking for reason in reasons)
    return AssistantConfiguration(available=available, reasons=tuple(reasons))


def lock_timeout_ms() -> int:
    return _positive_int(str(getattr(settings, "ASSISTANT_LOCK_TIMEOUT_MS", "2000")), 2000, 5000)


def gemini_timeout_seconds() -> int:
    return _positive_int(str(getattr(settings, "GEMINI_TIMEOUT", "30")), 30, 120)


def gemini_max_retries() -> int:
    return _positive_int(str(getattr(settings, "GEMINI_MAX_RETRIES", "2")), 2, 5)


def max_steps() -> int:
    return _positive_int(str(getattr(settings, "ASSISTANT_MAX_STEPS", "12")), 12, 20)


def max_tool_calls() -> int:
    return _positive_int(str(getattr(settings, "ASSISTANT_MAX_TOOL_CALLS", "8")), 8, 20)


def max_execution_seconds() -> int:
    return _positive_int(str(getattr(settings, "ASSISTANT_MAX_EXECUTION_TIME", "20")), 20, 60)


def classify_provider_failure(*, status_code: int | None, provider_status: str) -> ProviderFailure:
    """Separate credential rejection from quota, timeout, and network failure."""

    status = provider_status.strip().upper().replace("-", "_").replace(" ", "_")
    if status_code in {401, 403} or status in {
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "API_KEY_INVALID",
        "INVALID_API_KEY",
    }:
        return ProviderFailure.CREDENTIAL
    if status_code == 429 or status in {"RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"}:
        return ProviderFailure.QUOTA
    if status_code == 408 or status in {"DEADLINE_EXCEEDED", "TIMEOUT"}:
        return ProviderFailure.TIMEOUT
    if status_code in {502, 503, 504} or status in {"UNAVAILABLE", "NETWORK"}:
        return ProviderFailure.NETWORK
    return ProviderFailure.PROVIDER

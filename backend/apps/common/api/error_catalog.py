from __future__ import annotations

import re
from typing import Any

from apps.common.api.error_messages_my import MESSAGES

_MYANMAR = re.compile(r"^(?:my|mm)(?:-|$)")
_ACTION = {"Check-in": "ဝင်ရောက်မှု", "Check-out": "ထွက်ခွာမှု"}
_TEMPLATES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^Enter a value from (?P<minimum>\d+) to (?P<maximum>\d+)\.$"),
        "{minimum} မှ {maximum} အတွင်း တန်ဖိုးတစ်ခု ထည့်ပါ။",
    ),
    (
        re.compile(r"^Ensure this field has no more than (?P<limit>\d+) characters\.$"),
        "ဤအကွက်တွင် အက္ခရာ {limit} လုံးထက် မပိုရ။",
    ),
    (
        re.compile(r"^(?P<action>Check-in|Check-out) time cannot be in the future\.$"),
        "{action}ချိန်သည် အနာဂတ်မဖြစ်ရ။",
    ),
    (
        re.compile(r"^(?P<action>Check-in|Check-out) can only be backdated 24 hours\.$"),
        "{action}ကို နာရီ ၂၄ သာ နောက်ကြောင်းပြန် မှတ်နိုင်သည်။",
    ),
)


def myanmar_messages() -> dict[str, str]:
    return MESSAGES


def wants_myanmar(request: Any) -> bool:
    """True when the first Accept-Language tag is Burmese."""

    header = _accept_language(request).replace("_", "-").strip().lower()
    if not header:
        return False
    tag = header.split(",")[0].split(";", 1)[0].strip()
    return _MYANMAR.match(tag) is not None


def translate_error_text(text: str, *, myanmar: bool) -> str:
    """Return the catalog sentence, or the original English when it is absent."""

    if not myanmar:
        return text
    key = " ".join(text.split())
    found = myanmar_messages().get(key)
    if found:
        return found
    for pattern, template in _TEMPLATES:
        match = pattern.fullmatch(key)
        if match is None:
            continue
        values = {name: _ACTION.get(value, value) for name, value in match.groupdict().items()}
        return template.format(**values)
    return text


def translate_error_fields(value: Any, *, myanmar: bool) -> Any:
    if not myanmar:
        return value
    if isinstance(value, str):
        return translate_error_text(value, myanmar=True)
    if isinstance(value, list):
        return [translate_error_fields(item, myanmar=True) for item in value]
    if isinstance(value, dict):
        return {str(key): translate_error_fields(item, myanmar=True) for key, item in value.items()}
    return value


def _accept_language(request: Any) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            value = headers.get("Accept-Language")
        except (AttributeError, TypeError):
            value = None
        if isinstance(value, str) and value.strip():
            return value
    meta = getattr(request, "META", None)
    if isinstance(meta, dict):
        raw = meta.get("HTTP_ACCEPT_LANGUAGE", "")
        return raw if isinstance(raw, str) else ""
    return ""

"""Assign catalog codes so staff do not type them.

A supplied code is kept. A blank code becomes an uppercase token from the
label, with a numeric suffix when that token is already used.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from rest_framework import serializers

TOKEN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
SKU_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
GYM_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")
REGISTER_CODE = re.compile(r"^[A-Z0-9_-]{1,32}$")
DEFAULT_REGISTER = "MAIN"

_SPLIT = re.compile(r"[^A-Z0-9]+")


class OptionalPatternField(serializers.RegexField):
    """A pattern field that treats a blank value as omitted."""

    def __init__(self, regex: str | re.Pattern[str], **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("default", "")
        self.pattern = re.compile(regex) if isinstance(regex, str) else regex
        super().__init__(regex, **kwargs)

    def to_internal_value(self, data: Any) -> str:
        value = serializers.CharField.to_internal_value(self, data)
        if value == "":
            return ""
        if self.pattern.search(value) is None:
            self.fail("invalid")
        return value


def resolve_code(
    supplied: str,
    existing: Iterable[str],
    label: str,
    *,
    prefix: str,
    reserved: Iterable[str] = (),
    separator: str = "_",
    pattern: re.Pattern[str] = TOKEN,
    limit: int = 32,
) -> str:
    cleaned = supplied.strip()
    if cleaned:
        return cleaned
    return allocate_code(
        existing,
        label,
        prefix=prefix,
        reserved=reserved,
        separator=separator,
        pattern=pattern,
        limit=limit,
    )


def allocate_code(
    existing: Iterable[str],
    label: str,
    *,
    prefix: str,
    reserved: Iterable[str] = (),
    separator: str = "_",
    pattern: re.Pattern[str] = TOKEN,
    limit: int = 32,
) -> str:
    taken = {str(item).strip().upper() for item in existing}
    taken.update(str(item).strip().upper() for item in reserved)
    base = _stem(
        label,
        prefix=prefix,
        separator=separator,
        pattern=pattern,
        limit=limit,
    )
    candidate = base
    number = 2
    while candidate in taken or pattern.fullmatch(candidate) is None:
        suffix = f"{separator}{number}"
        room = limit - len(suffix)
        stem = base[:room].rstrip(separator)
        candidate = f"{stem}{suffix}" if stem else f"{prefix[:room]}{suffix}"
        number += 1
        if number > 100_000:
            raise RuntimeError("No unused code is available.")
    return candidate


def _stem(
    label: str,
    *,
    prefix: str,
    separator: str,
    pattern: re.Pattern[str],
    limit: int,
) -> str:
    parts = [part for part in _SPLIT.split(label.upper()) if part]
    token = separator.join(parts)
    if not token or not token[0].isalpha():
        token = separator.join([prefix, token] if token else [prefix])
    token = token[:limit].rstrip(separator)
    if pattern.fullmatch(token):
        return token
    fallback = prefix[:limit]
    if pattern.fullmatch(fallback):
        return fallback
    widened = f"{prefix}{separator}X"
    return widened[:limit]

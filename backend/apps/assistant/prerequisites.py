from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldRequirement:
    name: str
    description: str
    required: bool = True
    kind: str = "string"


@dataclass(frozen=True)
class Prerequisite:
    """Fields a later phase can require. This framework adds no gym business rule."""

    code: str
    description: str
    fields: tuple[FieldRequirement, ...]


def missing_fields(prerequisite: Prerequisite, collected: Mapping[str, object]) -> tuple[str, ...]:
    missing: list[str] = []
    for field in prerequisite.fields:
        if not field.required:
            continue
        value = collected.get(field.name, None)
        if value is None or value == "":
            missing.append(field.name)
    return tuple(missing)

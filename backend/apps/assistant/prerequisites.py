from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldRequirement:
    name: str
    description: str
    required: bool = True
    kind: str = "string"
    item_kind: str = "string"
    item_fields: tuple[FieldRequirement, ...] = ()


def optional(name: str, description: str, kind: str = "string") -> FieldRequirement:
    """A field the model may send. The server still validates it."""

    return FieldRequirement(name, description, required=False, kind=kind)


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

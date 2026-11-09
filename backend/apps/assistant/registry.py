from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDefinition:
    """One staff operation the assistant may call.

    External side effects are named so callers can see them. The definition
    cannot mark those effects as covered by the database claim.
    """

    name: str
    description: str
    module: str
    operation: str
    permissions: tuple[str, ...]
    features: tuple[str, ...] = ()
    requires_recent_mfa: bool = False
    requires_active_owner: bool = False
    confirmation_required: bool = False
    approval_required: bool = False
    external_side_effects: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.operation not in {"read", "write"}:
            raise ValueError("Tool operation must be read or write.")
        if not tool.name or tool.name in self._tools:
            raise ValueError("Tool name must be unique.")
        if not tool.permissions and not tool.requires_active_owner:
            raise ValueError("Tool must name at least one permission.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def authorized(self, *, permission_codes: frozenset[str]) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if tool.permissions and set(tool.permissions).issubset(permission_codes)
        )


def closed_production_names() -> tuple[str, ...]:
    """Return the names registered after their security tests passed."""

    return ()


def production_registry() -> ToolRegistry:
    """Return the tools whose security tests have passed."""

    return ToolRegistry()

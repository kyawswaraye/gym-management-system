from __future__ import annotations

import uuid

from django.db import connection


class TenantContextError(RuntimeError):
    pass


def _require_atomic() -> None:
    if not connection.in_atomic_block:
        raise TenantContextError("Tenant context requires an active transaction")


def set_identity_context(*, user_id: uuid.UUID, request_id: str) -> None:
    _require_atomic()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.user_id', %s, true), set_config('app.request_id', %s, true)",
            [str(user_id), request_id],
        )


def set_gym_context(*, gym_id: uuid.UUID) -> None:
    _require_atomic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])


def clear_gym_context() -> None:
    """Remove the transaction-local gym id before returning to a caller.

    ``set_config(..., true)`` lasts until the surrounding transaction ends. A
    management command that commits an inner savepoint must clear the value
    itself so a caller's still-open transaction does not keep the last gym.
    """

    _require_atomic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', '', true)")

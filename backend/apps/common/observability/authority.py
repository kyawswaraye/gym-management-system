"""Mark actor and gym ids only after authentication has established them."""

from __future__ import annotations

import uuid
from typing import Any


def note_observability_authority(
    request: object, *, actor_id: uuid.UUID, gym_id: uuid.UUID | None = None
) -> None:
    actor = str(actor_id)
    gym = None if gym_id is None else str(gym_id)
    targets: list[Any] = [request]
    underlying = getattr(request, "_request", None)
    if underlying is not None and underlying is not request:
        targets.append(underlying)
    for target in targets:
        target.observability_actor_id = actor
        if gym is not None:
            target.observability_gym_id = gym

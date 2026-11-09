from __future__ import annotations

from datetime import datetime

from django.db import transaction

from apps.assistant.models import Conversation, Goal, Workflow, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership


def open_staff_goal(
    *,
    gym: Gym,
    actor: GymMembership,
    intent: str,
    expires_at: datetime,
    title: str = "",
) -> tuple[Conversation, Goal, WorkflowStep]:
    """Create the bookkeeping rows for one staff goal. This sells nothing."""

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        conversation = Conversation.objects.create(
            gym=gym,
            created_by_membership=actor,
            title=title,
        )
        goal = Goal.objects.create(
            gym=gym,
            conversation=conversation,
            created_by_membership=actor,
            intent=intent,
            expires_at=expires_at,
        )
        workflow = Workflow.objects.create(gym=gym, goal=goal)
        step = WorkflowStep.objects.create(
            gym=gym,
            workflow=workflow,
            goal=goal,
            position=1,
            tool_name="collect_fields",
        )
        return conversation, goal, step

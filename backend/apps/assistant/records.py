from __future__ import annotations

from datetime import datetime
from typing import cast

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.assistant.models import Conversation, Goal, Message, Workflow, WorkflowStep
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


def open_conversation(*, gym: Gym, actor: GymMembership, title: str) -> Conversation:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Conversation.objects.create(
            gym=gym,
            created_by_membership=actor,
            title=title[:120],
        )


def append_message(
    *,
    gym: Gym,
    conversation: Conversation,
    actor: GymMembership | None,
    role: str,
    body: str,
    goal: Goal | None = None,
) -> Message:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Message.objects.create(
            gym=gym,
            conversation=conversation,
            goal=goal,
            author_membership=actor,
            role=role,
            body=body,
        )


def open_confirmation(
    *,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    intent: str,
    tool_name: str,
    required_input: dict[str, object],
    expires_at: datetime,
) -> tuple[Goal, WorkflowStep] | None:
    """Store one waiting confirmation. A second active goal is refused."""

    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            if Goal.objects.filter(gym=gym, conversation=conversation, is_active=True).exists():
                return None
            goal = Goal.objects.create(
                gym=gym,
                conversation=conversation,
                created_by_membership=actor,
                intent=intent[:200],
                status=Goal.Status.WAITING_CONFIRMATION,
                expires_at=expires_at,
            )
            workflow = Workflow.objects.create(
                gym=gym,
                goal=goal,
                status=Goal.Status.WAITING_CONFIRMATION,
            )
            step = WorkflowStep.objects.create(
                gym=gym,
                workflow=workflow,
                goal=goal,
                position=1,
                tool_name=tool_name,
                status=WorkflowStep.Status.WAITING_CONFIRMATION,
                required_input=required_input,
            )
            return goal, step
    except IntegrityError:
        return None


def close_goal(
    *,
    gym: Gym,
    goal: Goal,
    step: WorkflowStep,
    status: str,
    step_status: str,
    summary: str = "",
) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        now = timezone.now()
        Goal.objects.filter(pk=goal.pk, gym=gym).update(
            status=status,
            is_active=False,
            result_summary=summary[:4000],
            updated_at=now,
        )
        Workflow.objects.filter(goal=goal, gym=gym).update(status=status, updated_at=now)
        WorkflowStep.objects.filter(pk=step.pk, gym=gym).update(status=step_status, updated_at=now)


def stored_payload(step: WorkflowStep) -> dict[str, object]:
    raw = step.required_input
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return {}
    return cast(dict[str, object], payload)

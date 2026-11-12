from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet
from django.http import Http404

from apps.assistant.models import Conversation, Goal, Message, ToolExecution, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership


def _scoped(gym: Gym) -> None:
    set_gym_context(gym_id=gym.id)


def may_read_conversation(*, actor: GymMembership, conversation: Conversation) -> bool:
    """The author can read the chat. An active gym owner can review the gym's chats.

    No other same-gym role receives another staff member's conversation.
    """

    if conversation.gym_id != actor.gym_id:
        return False
    if conversation.created_by_membership_id == actor.id:
        return True
    return bool(
        actor.is_owner
        and actor.status == GymMembership.Status.ACTIVE
        and actor.gym_id == conversation.gym_id
    )


def list_conversations(
    *,
    gym: Gym,
    actor: GymMembership,
    limit: int,
) -> QuerySet[Conversation]:
    _scoped(gym)
    rows = Conversation.objects.filter(gym=gym)
    if not (
        actor.is_owner and actor.status == GymMembership.Status.ACTIVE and actor.gym_id == gym.id
    ):
        rows = rows.filter(created_by_membership_id=actor.id)
    return rows.order_by("-created_at", "id")[:limit]


def get_conversation(
    *,
    gym: Gym,
    actor: GymMembership,
    conversation_id: UUID,
) -> Conversation:
    _scoped(gym)
    try:
        conversation = Conversation.objects.get(gym=gym, id=conversation_id)
    except Conversation.DoesNotExist as exc:
        raise Http404 from exc
    if not may_read_conversation(actor=actor, conversation=conversation):
        raise Http404
    return conversation


def list_messages(*, gym: Gym, conversation: Conversation) -> QuerySet[Message]:
    _scoped(gym)
    return (
        Message.objects.filter(gym=gym, conversation=conversation)
        .exclude(role=Message.Role.SYSTEM, body__startswith="workflow-state v1\n")
        .order_by("created_at", "id")
    )


def list_goals(*, gym: Gym, conversation: Conversation) -> QuerySet[Goal]:
    _scoped(gym)
    return Goal.objects.filter(gym=gym, conversation=conversation).order_by("created_at", "id")


def get_goal(*, gym: Gym, actor: GymMembership, goal_id: UUID) -> Goal:
    _scoped(gym)
    try:
        goal = Goal.objects.select_related("conversation").get(gym=gym, id=goal_id)
    except Goal.DoesNotExist as exc:
        raise Http404 from exc
    if not may_read_conversation(actor=actor, conversation=goal.conversation):
        raise Http404
    return goal


def list_steps(*, gym: Gym, goal: Goal) -> QuerySet[WorkflowStep]:
    _scoped(gym)
    return WorkflowStep.objects.filter(gym=gym, goal=goal).order_by("position", "id")


def list_executions(*, gym: Gym, goal: Goal) -> QuerySet[ToolExecution]:
    _scoped(gym)
    return ToolExecution.objects.filter(gym=gym, goal=goal).order_by("created_at", "id")

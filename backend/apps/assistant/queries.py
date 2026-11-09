from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet
from django.http import Http404

from apps.assistant.models import Conversation, Goal, Message, ToolExecution, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym


def _scoped(gym: Gym) -> None:
    set_gym_context(gym_id=gym.id)


def list_conversations(*, gym: Gym, limit: int) -> QuerySet[Conversation]:
    _scoped(gym)
    return Conversation.objects.filter(gym=gym).order_by("-created_at", "id")[:limit]


def get_conversation(*, gym: Gym, conversation_id: UUID) -> Conversation:
    _scoped(gym)
    try:
        return Conversation.objects.get(gym=gym, id=conversation_id)
    except Conversation.DoesNotExist as exc:
        raise Http404 from exc


def list_messages(*, gym: Gym, conversation: Conversation) -> QuerySet[Message]:
    _scoped(gym)
    return Message.objects.filter(gym=gym, conversation=conversation).order_by("created_at", "id")


def list_goals(*, gym: Gym, conversation: Conversation) -> QuerySet[Goal]:
    _scoped(gym)
    return Goal.objects.filter(gym=gym, conversation=conversation).order_by("created_at", "id")


def get_goal(*, gym: Gym, goal_id: UUID) -> Goal:
    _scoped(gym)
    try:
        return Goal.objects.get(gym=gym, id=goal_id)
    except Goal.DoesNotExist as exc:
        raise Http404 from exc


def list_steps(*, gym: Gym, goal: Goal) -> QuerySet[WorkflowStep]:
    _scoped(gym)
    return WorkflowStep.objects.filter(gym=gym, goal=goal).order_by("position", "id")


def list_executions(*, gym: Gym, goal: Goal) -> QuerySet[ToolExecution]:
    _scoped(gym)
    return ToolExecution.objects.filter(gym=gym, goal=goal).order_by("created_at", "id")

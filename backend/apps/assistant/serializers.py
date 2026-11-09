from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from rest_framework import serializers

from apps.assistant.models import Conversation, Goal, Message, ToolExecution, WorkflowStep


class StrictQuerySerializer(serializers.Serializer[dict[str, object]]):
    def to_internal_value(self, data: Any) -> dict[str, object]:
        unknown = set(data) - set(self.fields) if isinstance(data, Mapping) else set()
        if unknown:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown)}
            )
        return cast(dict[str, object], super().to_internal_value(data))


class ConversationListQuerySerializer(StrictQuerySerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)


class AssistantStatusSerializer(serializers.Serializer[dict[str, object]]):
    available = serializers.BooleanField()
    reasons = serializers.ListField(child=serializers.CharField())


class ConversationSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    title = serializers.CharField()
    created_at = serializers.DateTimeField()


class MessageSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    role = serializers.CharField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()


class GoalSummarySerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    intent = serializers.CharField()
    status = serializers.CharField()
    version = serializers.IntegerField()
    is_active = serializers.BooleanField()
    expires_at = serializers.DateTimeField()


class StepSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    tool_name = serializers.CharField()
    status = serializers.CharField()
    attempt_number = serializers.IntegerField()


class ExecutionSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    tool_name = serializers.CharField()
    attempt_number = serializers.IntegerField()
    effect_reference = serializers.CharField()
    external_side_effects = serializers.ListField(child=serializers.CharField())
    covers_external_side_effects = serializers.BooleanField()


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True)
    goals = GoalSummarySerializer(many=True)


class GoalDetailSerializer(GoalSummarySerializer):
    result_summary = serializers.CharField()
    missing_information = serializers.ListField(child=serializers.CharField())
    steps = StepSerializer(many=True)
    executions = ExecutionSerializer(many=True)


class AssistantStatusEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = AssistantStatusSerializer()
    message = serializers.CharField()


class ConversationListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ConversationSerializer(many=True)
    message = serializers.CharField()


class ConversationDetailEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ConversationDetailSerializer()
    message = serializers.CharField()


class GoalDetailEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = GoalDetailSerializer()
    message = serializers.CharField()


def conversation_data(row: Conversation) -> dict[str, object]:
    return {
        "id": row.id,
        "status": row.status,
        "title": row.title,
        "created_at": row.created_at,
    }


def message_data(row: Message) -> dict[str, object]:
    return {
        "id": row.id,
        "role": row.role,
        "body": row.body,
        "created_at": row.created_at,
    }


def goal_summary(row: Goal) -> dict[str, object]:
    return {
        "id": row.id,
        "intent": row.intent,
        "status": row.status,
        "version": row.version,
        "is_active": row.is_active,
        "expires_at": row.expires_at,
    }


def step_data(row: WorkflowStep) -> dict[str, object]:
    return {
        "id": row.id,
        "position": row.position,
        "tool_name": row.tool_name,
        "status": row.status,
        "attempt_number": row.attempt_number,
    }


def execution_data(row: ToolExecution) -> dict[str, object]:
    effects = row.external_side_effects
    names = [str(item) for item in effects] if isinstance(effects, list) else []
    return {
        "id": row.id,
        "tool_name": row.tool_name,
        "attempt_number": row.attempt_number,
        "effect_reference": row.effect_reference,
        "external_side_effects": names,
        "covers_external_side_effects": row.covers_external_side_effects,
    }

"""Read-only assistant routes. None of these continue a goal or call Gemini."""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant.configuration import assistant_configuration
from apps.assistant.queries import (
    get_conversation,
    get_goal,
    list_conversations,
    list_executions,
    list_goals,
    list_messages,
    list_steps,
)
from apps.assistant.serializers import (
    AssistantStatusEnvelopeSerializer,
    ConversationDetailEnvelopeSerializer,
    ConversationListEnvelopeSerializer,
    ConversationListQuerySerializer,
    GoalDetailEnvelopeSerializer,
    conversation_data,
    execution_data,
    goal_summary,
    message_data,
    step_data,
)
from apps.common.api.exceptions import RequestRejected
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.api.validation import validated
from apps.tenants.models import Gym
from apps.users.authentication import StaffJWTAuthentication


def _gym(request: Request) -> Gym:
    gym = getattr(request, "gym", None)
    if not isinstance(gym, Gym):
        raise PermissionDenied()
    return gym


def _reject_idempotency(request: Request) -> None:
    if request.headers.get("Idempotency-Key"):
        raise RequestRejected({"Idempotency-Key": ["Idempotency-Key is not accepted on a read."]})


class AssistantStatusView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="assistant_status",
        responses={
            200: AssistantStatusEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        _reject_idempotency(request)
        _gym(request)
        configuration = assistant_configuration()
        return success_response(
            {"available": configuration.available, "reasons": list(configuration.reasons)}
        )


class ConversationListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="assistant_conversations_list",
        parameters=[ConversationListQuerySerializer],
        responses={
            200: ConversationListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        _reject_idempotency(request)
        gym = _gym(request)
        query = validated(ConversationListQuerySerializer(data=request.query_params))
        rows = list_conversations(gym=gym, limit=int(query["limit"]))
        return success_response([conversation_data(row) for row in rows])


class ConversationDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="assistant_conversations_read",
        responses={
            200: ConversationDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        gym = _gym(request)
        conversation = get_conversation(gym=gym, conversation_id=id)
        payload = conversation_data(conversation)
        messages = list_messages(gym=gym, conversation=conversation)
        goals = list_goals(gym=gym, conversation=conversation)
        payload["messages"] = [message_data(row) for row in messages]
        payload["goals"] = [goal_summary(row) for row in goals]
        return success_response(payload)


class GoalDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="assistant_goals_read",
        responses={
            200: GoalDetailEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        gym = _gym(request)
        goal = get_goal(gym=gym, goal_id=id)
        payload = goal_summary(goal)
        payload["result_summary"] = goal.result_summary
        payload["missing_information"] = list(goal.missing_information)
        payload["steps"] = [step_data(row) for row in list_steps(gym=gym, goal=goal)]
        payload["executions"] = [execution_data(row) for row in list_executions(gym=gym, goal=goal)]
        return success_response(payload)

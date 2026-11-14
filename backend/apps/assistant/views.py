"""Assistant routes. Reads stay read-only. Turns call Gemini outside a transaction."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant.configuration import assistant_configuration
from apps.assistant.entitlement import gym_ai_entitled, require_gym_ai
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
    ConfirmationEnvelopeSerializer,
    ConfirmRequestSerializer,
    ConversationDetailEnvelopeSerializer,
    ConversationListEnvelopeSerializer,
    ConversationListQuerySerializer,
    GoalDetailEnvelopeSerializer,
    RejectRequestSerializer,
    TurnEnvelopeSerializer,
    TurnRequestSerializer,
    conversation_data,
    execution_data,
    goal_summary,
    message_data,
    step_data,
)
from apps.assistant.throttling import (
    AssistantGymReadThrottle,
    AssistantGymTurnThrottle,
    AssistantReadThrottle,
    AssistantTurnThrottle,
)
from apps.assistant.turns import confirm_step, reject_step, run_turn, waiting_confirmation
from apps.common.api.exceptions import RequestRejected
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.common.api.validation import validated
from apps.tenants.models import Gym, GymMembership
from apps.users.authentication import StaffJWTAuthentication


def _gym(request: Request) -> Gym:
    gym = getattr(request, "gym", None)
    if not isinstance(gym, Gym):
        raise PermissionDenied()
    return gym


def _actor(request: Request) -> GymMembership:
    actor = getattr(request, "gym_membership", None)
    if not isinstance(actor, GymMembership) or actor.gym_id != _gym(request).id:
        raise PermissionDenied()
    return actor


def _reject_idempotency(request: Request) -> None:
    if request.headers.get("Idempotency-Key"):
        raise RequestRejected({"Idempotency-Key": ["Idempotency-Key is not accepted on a read."]})


class AssistantStatusView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantReadThrottle, AssistantGymReadThrottle]

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
        gym = _gym(request)
        configuration = assistant_configuration()
        entitled = gym_ai_entitled(gym.id)
        reasons = list(configuration.reasons)
        if not entitled:
            reasons.append("not_entitled")
        return success_response(
            {"available": configuration.available and entitled, "reasons": reasons}
        )


class ConversationListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantReadThrottle, AssistantGymReadThrottle]

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
        require_gym_ai(gym)
        actor = _actor(request)
        query = validated(ConversationListQuerySerializer(data=request.query_params))
        rows = list_conversations(gym=gym, actor=actor, limit=int(query["limit"]))
        return success_response([conversation_data(row) for row in rows])


class ConversationDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantReadThrottle, AssistantGymReadThrottle]

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
        require_gym_ai(gym)
        actor = _actor(request)
        conversation = get_conversation(gym=gym, actor=actor, conversation_id=id)
        payload = conversation_data(conversation)
        messages = list_messages(gym=gym, conversation=conversation)
        goals = list_goals(gym=gym, conversation=conversation)
        payload["messages"] = [message_data(row) for row in messages]
        payload["goals"] = [goal_summary(row) for row in goals]
        membership = getattr(request, "gym_membership", None)
        payload["pending_confirmation"] = (
            waiting_confirmation(gym=gym, conversation=conversation, actor=membership)
            if isinstance(membership, GymMembership)
            else None
        )
        return success_response(payload)


class GoalDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantReadThrottle, AssistantGymReadThrottle]

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
        require_gym_ai(gym)
        actor = _actor(request)
        goal = get_goal(gym=gym, actor=actor, goal_id=id)
        payload = goal_summary(goal)
        payload["result_summary"] = goal.result_summary
        payload["missing_information"] = list(goal.missing_information)
        payload["steps"] = [step_data(row) for row in list_steps(gym=gym, goal=goal)]
        payload["executions"] = [execution_data(row) for row in list_executions(gym=gym, goal=goal)]
        return success_response(payload)


class _StaffCommandView(APIView):
    """Authenticate inside a transaction, then run the handler outside one.

    Gemini and finance commands cannot stay inside the request transaction.
    Staff authentication still needs one to set the tenant context.
    """

    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        self.args = args
        self.kwargs = kwargs
        rest_request = self.initialize_request(request, *args, **kwargs)
        self.request = rest_request
        self.headers = self.default_response_headers
        try:
            with transaction.atomic():
                self.initial(rest_request, *args, **kwargs)
            method = rest_request.method
            if method is not None and method.lower() in self.http_method_names:
                handler = getattr(self, method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed
            response = handler(rest_request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)
        self.response = self.finalize_response(rest_request, response, *args, **kwargs)
        return self.response


class AssistantTurnView(_StaffCommandView):
    throttle_classes = [AssistantTurnThrottle, AssistantGymTurnThrottle]

    @extend_schema(
        operation_id="assistant_turns_create",
        request=TurnRequestSerializer,
        responses={
            200: TurnEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            503: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        _reject_idempotency(request)
        require_gym_ai(_gym(request))
        body = validated(TurnRequestSerializer(data=request.data))
        conversation_id = body.get("conversation_id")
        result = run_turn(
            request=request,
            view=self,
            message=str(body["message"]),
            conversation_id=conversation_id if isinstance(conversation_id, UUID) else None,
        )
        return success_response(result)


class AssistantConfirmView(_StaffCommandView):
    throttle_classes = [AssistantTurnThrottle, AssistantGymTurnThrottle]

    @extend_schema(
        operation_id="assistant_confirmations_confirm",
        request=ConfirmRequestSerializer,
        responses={
            200: ConfirmationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        require_gym_ai(_gym(request))
        validated(ConfirmRequestSerializer(data=request.data))
        return success_response(confirm_step(request=request, view=self, step_id=id))


class AssistantRejectView(_StaffCommandView):
    throttle_classes = [AssistantTurnThrottle, AssistantGymTurnThrottle]

    @extend_schema(
        operation_id="assistant_confirmations_reject",
        request=RejectRequestSerializer,
        responses={
            200: ConfirmationEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_idempotency(request)
        require_gym_ai(_gym(request))
        validated(RejectRequestSerializer(data=request.data))
        return success_response(reject_step(request=request, view=self, step_id=id))

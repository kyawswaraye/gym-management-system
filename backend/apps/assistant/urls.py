from django.urls import path

from apps.assistant.views import (
    AssistantConfirmView,
    AssistantRejectView,
    AssistantStatusView,
    AssistantTurnView,
    ConversationDetailView,
    ConversationListView,
    GoalDetailView,
)

urlpatterns = [
    path("assistant/status/", AssistantStatusView.as_view(), name="assistant-status"),
    path(
        "assistant/conversations/",
        ConversationListView.as_view(),
        name="assistant-conversations",
    ),
    path(
        "assistant/conversations/<uuid:id>/",
        ConversationDetailView.as_view(),
        name="assistant-conversation-detail",
    ),
    path("assistant/goals/<uuid:id>/", GoalDetailView.as_view(), name="assistant-goal-detail"),
    path("assistant/turns/", AssistantTurnView.as_view(), name="assistant-turns"),
    path(
        "assistant/confirmations/<uuid:id>/confirm/",
        AssistantConfirmView.as_view(),
        name="assistant-confirmation-confirm",
    ),
    path(
        "assistant/confirmations/<uuid:id>/reject/",
        AssistantRejectView.as_view(),
        name="assistant-confirmation-reject",
    ),
]

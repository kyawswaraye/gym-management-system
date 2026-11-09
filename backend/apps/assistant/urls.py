from django.urls import path

from apps.assistant.views import (
    AssistantStatusView,
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
]

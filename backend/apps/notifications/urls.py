from django.urls import path

from apps.notifications.member_views import MemberNotificationListView, MemberNotificationReadView

urlpatterns = [
    path(
        "member/notifications/",
        MemberNotificationListView.as_view(),
        name="member-notification-list",
    ),
    path(
        "member/notifications/<uuid:id>/read/",
        MemberNotificationReadView.as_view(),
        name="member-notification-read",
    ),
]

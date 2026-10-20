from django.urls import path

from apps.announcements.views import (
    AnnouncementCancelView,
    AnnouncementDetailView,
    AnnouncementListCreateView,
    AnnouncementPublishView,
)

urlpatterns = [
    path("announcements/", AnnouncementListCreateView.as_view(), name="announcement-list"),
    path("announcements/<uuid:id>/", AnnouncementDetailView.as_view(), name="announcement-detail"),
    path(
        "announcements/<uuid:id>/publish/",
        AnnouncementPublishView.as_view(),
        name="announcement-publish",
    ),
    path(
        "announcements/<uuid:id>/cancel/",
        AnnouncementCancelView.as_view(),
        name="announcement-cancel",
    ),
]

from django.urls import path

from apps.classes.booking_views import (
    ClassBookingAttendView,
    ClassBookingCancelView,
    ClassBookingListCreateView,
    ClassBookingNoShowView,
)
from apps.classes.views import (
    ClassOccurrenceCancelView,
    ClassOccurrenceListView,
    ClassScheduleDetailView,
    ClassScheduleListCreateView,
    ClassTypeDetailView,
    ClassTypeListCreateView,
)

urlpatterns = [
    path("class-types/", ClassTypeListCreateView.as_view(), name="class-type-list"),
    path("class-types/<uuid:id>/", ClassTypeDetailView.as_view(), name="class-type-detail"),
    path("class-schedules/", ClassScheduleListCreateView.as_view(), name="class-schedule-list"),
    path(
        "class-schedules/<uuid:id>/",
        ClassScheduleDetailView.as_view(),
        name="class-schedule-detail",
    ),
    path("class-occurrences/", ClassOccurrenceListView.as_view(), name="class-occurrence-list"),
    path(
        "class-occurrences/<uuid:id>/cancel/",
        ClassOccurrenceCancelView.as_view(),
        name="class-occurrence-cancel",
    ),
    path("class-bookings/", ClassBookingListCreateView.as_view(), name="class-booking-list"),
    path(
        "class-bookings/<uuid:id>/attend/",
        ClassBookingAttendView.as_view(),
        name="class-booking-attend",
    ),
    path(
        "class-bookings/<uuid:id>/no-show/",
        ClassBookingNoShowView.as_view(),
        name="class-booking-no-show",
    ),
    path(
        "class-bookings/<uuid:id>/cancel/",
        ClassBookingCancelView.as_view(),
        name="class-booking-cancel",
    ),
]

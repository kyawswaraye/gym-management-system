from django.urls import path

from apps.members.views import (
    DashboardView,
    MemberArchiveView,
    MemberDetailView,
    MemberListCreateView,
    MembershipCancelView,
    MembershipDetailView,
    MembershipExpiryView,
    MembershipListCreateView,
    MembershipResumeView,
    MembershipSuspendView,
    MemberSummaryView,
    PaymentActionView,
    PaymentListCreateView,
    PlanArchiveView,
    PlanDetailView,
    PlanListCreateView,
)

urlpatterns = [
    path("members/", MemberListCreateView.as_view(), name="member-list"),
    path("members/<uuid:id>/", MemberDetailView.as_view(), name="member-detail"),
    path("members/<uuid:id>/archive/", MemberArchiveView.as_view(), name="member-archive"),
    path("members/<uuid:member_id>/summary/", MemberSummaryView.as_view(), name="member-summary"),
    path("membership-plans/", PlanListCreateView.as_view(), name="membership-plan-list"),
    path(
        "membership-plans/<uuid:id>/",
        PlanDetailView.as_view(),
        name="membership-plan-detail",
    ),
    path(
        "membership-plans/<uuid:id>/archive/",
        PlanArchiveView.as_view(),
        name="membership-plan-archive",
    ),
    path("memberships/", MembershipListCreateView.as_view(), name="membership-list"),
    path("memberships/expiry/", MembershipExpiryView.as_view(), name="membership-expiry"),
    path("memberships/<uuid:id>/", MembershipDetailView.as_view(), name="membership-detail"),
    path(
        "memberships/<uuid:id>/cancel/",
        MembershipCancelView.as_view(),
        name="membership-cancel",
    ),
    path(
        "memberships/<uuid:id>/suspend/",
        MembershipSuspendView.as_view(),
        name="membership-suspend",
    ),
    path(
        "memberships/<uuid:id>/resume/",
        MembershipResumeView.as_view(),
        name="membership-resume",
    ),
    path("payments/", PaymentListCreateView.as_view(), name="payment-list"),
    path(
        "payments/<uuid:payment_id>/<str:action>/",
        PaymentActionView.as_view(),
        name="payment-action",
    ),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
]

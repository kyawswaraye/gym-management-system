from django.urls import path

from apps.members.views import (
    DashboardView,
    MemberArchiveView,
    MemberDetailView,
    MemberListCreateView,
    MembershipActionView,
    MembershipListCreateView,
    MemberSummaryView,
    PaymentActionView,
    PaymentListCreateView,
    PlanArchiveView,
    PlanDetailView,
    PlanListCreateView,
)

urlpatterns = [
    path("members/", MemberListCreateView.as_view(), name="member-list"),
    path("members/<uuid:member_id>/", MemberDetailView.as_view(), name="member-detail"),
    path("members/<uuid:member_id>/archive/", MemberArchiveView.as_view(), name="member-archive"),
    path("members/<uuid:member_id>/summary/", MemberSummaryView.as_view(), name="member-summary"),
    path("membership-plans/", PlanListCreateView.as_view(), name="membership-plan-list"),
    path(
        "membership-plans/<uuid:plan_id>/",
        PlanDetailView.as_view(),
        name="membership-plan-detail",
    ),
    path(
        "membership-plans/<uuid:plan_id>/archive/",
        PlanArchiveView.as_view(),
        name="membership-plan-archive",
    ),
    path("memberships/", MembershipListCreateView.as_view(), name="membership-list"),
    path("memberships/expiry/", MembershipListCreateView.as_view(), name="membership-expiry"),
    path(
        "memberships/<uuid:membership_id>/<str:action>/",
        MembershipActionView.as_view(),
        name="membership-action",
    ),
    path("payments/", PaymentListCreateView.as_view(), name="payment-list"),
    path(
        "payments/<uuid:payment_id>/<str:action>/",
        PaymentActionView.as_view(),
        name="payment-action",
    ),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
]

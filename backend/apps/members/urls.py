from django.urls import path

from apps.members.device_views import (
    DeviceCheckInChallengeView,
    DeviceCheckInIngestView,
    DisplayDeviceListCreateView,
    DisplayDeviceRevokeView,
    DisplayDeviceRotateView,
    IntegrationDeviceListCreateView,
    IntegrationDeviceRevokeView,
    IntegrationDeviceRotateView,
)
from apps.members.scan_views import MemberCheckInScanView
from apps.members.views import (
    CheckInChallengeIssueView,
    CheckInCheckoutView,
    CheckInDetailView,
    CheckInListCreateView,
    DashboardView,
    MemberArchiveView,
    MemberCheckInHistoryView,
    MemberDetailView,
    MemberListCreateView,
    MemberPaymentHistoryView,
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
    path("members/<uuid:id>/summary/", MemberSummaryView.as_view(), name="member-summary"),
    path(
        "members/<uuid:id>/payments/",
        MemberPaymentHistoryView.as_view(),
        name="member-payment-history",
    ),
    path(
        "members/<uuid:id>/check-ins/",
        MemberCheckInHistoryView.as_view(),
        name="member-check-in-history",
    ),
    path("check-ins/", CheckInListCreateView.as_view(), name="check-in-list"),
    path("check-ins/<uuid:id>/", CheckInDetailView.as_view(), name="check-in-detail"),
    path(
        "check-ins/<uuid:id>/check-out/",
        CheckInCheckoutView.as_view(),
        name="check-in-check-out",
    ),
    path(
        "check-in-challenges/",
        CheckInChallengeIssueView.as_view(),
        name="check-in-challenge-issue",
    ),
    path(
        "member/check-ins/scan/",
        MemberCheckInScanView.as_view(),
        name="member-check-in-scan",
    ),
    path(
        "check-in-display-devices/",
        DisplayDeviceListCreateView.as_view(),
        name="check-in-display-device-list",
    ),
    path(
        "check-in-display-devices/<uuid:id>/rotate-credential/",
        DisplayDeviceRotateView.as_view(),
        name="check-in-display-device-rotate",
    ),
    path(
        "check-in-display-devices/<uuid:id>/revoke/",
        DisplayDeviceRevokeView.as_view(),
        name="check-in-display-device-revoke",
    ),
    path(
        "device/check-in-challenges/",
        DeviceCheckInChallengeView.as_view(),
        name="device-check-in-challenge-issue",
    ),
    path(
        "check-in-integration-devices/",
        IntegrationDeviceListCreateView.as_view(),
        name="check-in-integration-device-list",
    ),
    path(
        "check-in-integration-devices/<uuid:id>/rotate-credential/",
        IntegrationDeviceRotateView.as_view(),
        name="check-in-integration-device-rotate",
    ),
    path(
        "check-in-integration-devices/<uuid:id>/revoke/",
        IntegrationDeviceRevokeView.as_view(),
        name="check-in-integration-device-revoke",
    ),
    path(
        "device/check-ins/",
        DeviceCheckInIngestView.as_view(),
        name="device-check-in-ingest",
    ),
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

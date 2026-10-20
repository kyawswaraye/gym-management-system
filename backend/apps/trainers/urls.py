from django.urls import path

from apps.trainers.package_views import (
    MemberPackageDetailView,
    MemberPackageLedgerAdjustView,
    MemberPackageLedgerView,
    MemberPackageListCreateView,
)
from apps.trainers.pt_booking_views import (
    PTBookingAttendView,
    PTBookingCancelView,
    PTBookingDetailView,
    PTBookingListCreateView,
    PTBookingNoShowView,
)
from apps.trainers.pt_commission_views import (
    PTCommissionDetailView,
    PTCommissionListView,
    PTCommissionRecordPaymentView,
)
from apps.trainers.pt_sale_views import (
    PTSaleApproveView,
    PTSaleCancelView,
    PTSaleDetailView,
    PTSaleListCreateView,
    PTSaleRejectView,
    PTSaleSubmitView,
    PTSaleVoidView,
)
from apps.trainers.views import (
    ServicePackageListCreateView,
    TrainerAssignedMembersView,
    TrainerAssignmentCancelView,
    TrainerAssignmentDetailView,
    TrainerAssignmentEndView,
    TrainerAssignmentListCreateView,
    TrainerListView,
)

urlpatterns = [
    path("trainers/", TrainerListView.as_view(), name="trainer-list"),
    path(
        "trainers/<uuid:id>/assigned-members/",
        TrainerAssignedMembersView.as_view(),
        name="trainer-assigned-members",
    ),
    path(
        "trainer-assignments/",
        TrainerAssignmentListCreateView.as_view(),
        name="trainer-assignment-list",
    ),
    path(
        "trainer-assignments/<uuid:id>/",
        TrainerAssignmentDetailView.as_view(),
        name="trainer-assignment-detail",
    ),
    path(
        "trainer-assignments/<uuid:id>/end/",
        TrainerAssignmentEndView.as_view(),
        name="trainer-assignment-end",
    ),
    path(
        "trainer-assignments/<uuid:id>/cancel/",
        TrainerAssignmentCancelView.as_view(),
        name="trainer-assignment-cancel",
    ),
    path(
        "service-packages/",
        ServicePackageListCreateView.as_view(),
        name="service-package-list",
    ),
    path("pt-sales/", PTSaleListCreateView.as_view(), name="pt-sale-list"),
    path("pt-sales/<uuid:id>/", PTSaleDetailView.as_view(), name="pt-sale-detail"),
    path("pt-sales/<uuid:id>/cancel/", PTSaleCancelView.as_view(), name="pt-sale-cancel"),
    path("pt-sales/<uuid:id>/submit/", PTSaleSubmitView.as_view(), name="pt-sale-submit"),
    path("pt-sales/<uuid:id>/approve/", PTSaleApproveView.as_view(), name="pt-sale-approve"),
    path("pt-sales/<uuid:id>/reject/", PTSaleRejectView.as_view(), name="pt-sale-reject"),
    path("pt-sales/<uuid:id>/void/", PTSaleVoidView.as_view(), name="pt-sale-void"),
    path("pt-commissions/", PTCommissionListView.as_view(), name="pt-commission-list"),
    path(
        "pt-commissions/<uuid:id>/",
        PTCommissionDetailView.as_view(),
        name="pt-commission-detail",
    ),
    path(
        "pt-commissions/<uuid:id>/record-payment/",
        PTCommissionRecordPaymentView.as_view(),
        name="pt-commission-record-payment",
    ),
    path(
        "member-service-packages/",
        MemberPackageListCreateView.as_view(),
        name="member-service-package-list",
    ),
    path(
        "member-service-packages/<uuid:id>/",
        MemberPackageDetailView.as_view(),
        name="member-service-package-detail",
    ),
    path(
        "member-service-packages/<uuid:id>/ledger/",
        MemberPackageLedgerView.as_view(),
        name="member-service-package-ledger",
    ),
    path(
        "member-service-packages/<uuid:id>/ledger/adjust/",
        MemberPackageLedgerAdjustView.as_view(),
        name="member-service-package-ledger-adjust",
    ),
    path("pt-bookings/", PTBookingListCreateView.as_view(), name="pt-booking-list"),
    path("pt-bookings/<uuid:id>/", PTBookingDetailView.as_view(), name="pt-booking-detail"),
    path(
        "pt-bookings/<uuid:id>/attend/",
        PTBookingAttendView.as_view(),
        name="pt-booking-attend",
    ),
    path(
        "pt-bookings/<uuid:id>/no-show/",
        PTBookingNoShowView.as_view(),
        name="pt-booking-no-show",
    ),
    path(
        "pt-bookings/<uuid:id>/cancel/",
        PTBookingCancelView.as_view(),
        name="pt-booking-cancel",
    ),
]

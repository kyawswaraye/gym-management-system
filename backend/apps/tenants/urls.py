from django.urls import path

from apps.tenants.role_views import (
    MembershipRoleView,
    PermissionListView,
    RoleArchiveView,
    RoleDetailView,
    RoleListCreateView,
    RolePermissionReplaceView,
)
from apps.tenants.staff_views import (
    StaffActivateView,
    StaffDetailView,
    StaffInvitationAcceptView,
    StaffInvitationCreateView,
    StaffListView,
    StaffSuspendView,
)
from apps.tenants.views import (
    AdminDashboardView,
    AdminGymActivateView,
    AdminGymDetailView,
    AdminGymListCreateView,
    AdminGymLoginAccountsView,
    AdminGymOwnerCreateView,
    AdminGymSuspendView,
    AdminSubscriptionDetailView,
    AdminSubscriptionListView,
    GymLogoView,
    GymProfileView,
    GymSettingsView,
    OwnerInvitationAcceptView,
    OwnerInvitationCancelView,
    OwnerInvitationCreateView,
    OwnerListView,
    OwnerRevokeView,
)

urlpatterns = [
    path("gym/", GymProfileView.as_view(), name="gym-profile"),
    path("gym/logo/", GymLogoView.as_view(), name="gym-logo"),
    path("settings/", GymSettingsView.as_view(), name="gym-settings"),
    path("admin/dashboard/", AdminDashboardView.as_view(), name="admin-dashboard"),
    path("admin/gyms/", AdminGymListCreateView.as_view(), name="admin-gyms"),
    path(
        "admin/gyms/<uuid:gym_id>/",
        AdminGymDetailView.as_view(),
        name="admin-gym-detail",
    ),
    path(
        "admin/gyms/<uuid:gym_id>/activate/",
        AdminGymActivateView.as_view(),
        name="admin-gym-activate",
    ),
    path(
        "admin/gyms/<uuid:gym_id>/suspend/",
        AdminGymSuspendView.as_view(),
        name="admin-gym-suspend",
    ),
    path(
        "admin/gyms/<uuid:gym_id>/login-accounts/",
        AdminGymLoginAccountsView.as_view(),
        name="admin-gym-login-accounts",
    ),
    path(
        "admin/gyms/<uuid:gym_id>/owners/",
        AdminGymOwnerCreateView.as_view(),
        name="admin-gym-owner-create",
    ),
    path(
        "admin/subscriptions/",
        AdminSubscriptionListView.as_view(),
        name="admin-subscriptions",
    ),
    path(
        "admin/subscriptions/<uuid:id>/",
        AdminSubscriptionDetailView.as_view(),
        name="admin-subscription-detail",
    ),
    path("owners/", OwnerListView.as_view(), name="owners"),
    path(
        "owners/invitations/",
        OwnerInvitationCreateView.as_view(),
        name="owner-invitations",
    ),
    path(
        "owners/invitations/accept/",
        OwnerInvitationAcceptView.as_view(),
        name="owner-invitation-accept",
    ),
    path(
        "owners/<uuid:gym_membership_id>/cancel-invitation/",
        OwnerInvitationCancelView.as_view(),
        name="owner-invitation-cancel",
    ),
    path(
        "owners/<uuid:gym_membership_id>/revoke/",
        OwnerRevokeView.as_view(),
        name="owner-revoke",
    ),
    path("staff/", StaffListView.as_view(), name="staff-list"),
    path(
        "staff/invitations/",
        StaffInvitationCreateView.as_view(),
        name="staff-invitations",
    ),
    path(
        "staff/invitations/accept/",
        StaffInvitationAcceptView.as_view(),
        name="staff-invitation-accept",
    ),
    path("staff/<uuid:id>/", StaffDetailView.as_view(), name="staff-detail"),
    path(
        "staff/<uuid:id>/activate/",
        StaffActivateView.as_view(),
        name="staff-activate",
    ),
    path(
        "staff/<uuid:id>/suspend/",
        StaffSuspendView.as_view(),
        name="staff-suspend",
    ),
    path("roles/", RoleListCreateView.as_view(), name="roles"),
    path("roles/<uuid:id>/", RoleDetailView.as_view(), name="role-detail"),
    path("roles/<uuid:id>/archive/", RoleArchiveView.as_view(), name="role-archive"),
    path(
        "roles/<uuid:id>/permissions/",
        RolePermissionReplaceView.as_view(),
        name="role-permissions",
    ),
    path("permissions/", PermissionListView.as_view(), name="permissions"),
    path(
        "gym-memberships/<uuid:id>/roles/",
        MembershipRoleView.as_view(),
        name="membership-roles",
    ),
]

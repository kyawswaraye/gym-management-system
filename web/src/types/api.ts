import type { components } from "@/types/api.generated";

export type ApiErrorBody = {
  success: false;
  error: {
    code: string;
    message: string;
    fields?: Record<string, string[] | string>;
  };
  request_id?: string;
};

export type GymSummary = {
  id?: string;
  gym_id?: string;
  gym_membership_id?: string;
  name?: string;
  display_name?: string;
  status?: string;
  currency?: string;
  timezone?: string;
  gym?: { code?: string; name?: string };
};

export type CurrentUser = components["schemas"]["CurrentUserData"];
export type AdminDashboard = components["schemas"]["AdminDashboard"];
export type PlatformGym = components["schemas"]["AdminGymListItem"];
export type PlatformGymDetail = components["schemas"]["AdminGymDetail"];
export type PlatformGymPatch =
  components["schemas"]["PatchedAdminGymPatchRequest"];
export type PlatformSubscriptionDetail =
  components["schemas"]["AdminSubscriptionDetail"];
export type PlatformSubscriptionUpdate =
  components["schemas"]["PatchedAdminSubscriptionUpdateRequest"];
export type PlatformAuditItem = components["schemas"]["PlatformAuditItem"];
export type PlatformAuditMeta = components["schemas"]["PlatformAuditMeta"];
export type TenantAuditItem = components["schemas"]["TenantAuditItem"];
export type TenantAuditMeta = components["schemas"]["TenantAuditMeta"];
export type GymProfile = components["schemas"]["GymProfile"];
export type GymProfileUpdate =
  components["schemas"]["PatchedGymProfileUpdateRequest"];
export type GymSettings = components["schemas"]["GymSettings"];
export type GymSettingsUpdate =
  components["schemas"]["PatchedGymSettingsUpdateRequest"];
export type OwnerItem = components["schemas"]["OwnerItem"];
export type OwnerInvitation = components["schemas"]["OwnerInvitation"];
export type PlatformSubscription = components["schemas"]["AdminSubscription"];
export type GymProvisionRequest =
  components["schemas"]["AdminGymProvisionRequestRequest"];
export type GymProvisionResult = components["schemas"]["AdminGymProvisionData"];
export type StaffRecord = components["schemas"]["Staff"];
export type StaffListMeta = components["schemas"]["StaffListMeta"];
export type StaffInvitation = components["schemas"]["StaffInvitation"];
export type StaffInviteInput = components["schemas"]["StaffInviteInputRequest"];
export type StaffUpdate = components["schemas"]["PatchedStaffUpdateRequest"];
export type StaffAcceptInput = components["schemas"]["StaffAcceptInputRequest"];
export type Role = components["schemas"]["Role"];
export type RoleCreate = components["schemas"]["RoleCreateRequest"];
export type RoleUpdate = components["schemas"]["PatchedRoleUpdateRequest"];
export type Permission = components["schemas"]["Permission"];
export type MembershipRole = components["schemas"]["MembershipRole"];
export type CollectionMeta = components["schemas"]["CollectionMeta"];
export type Member = components["schemas"]["Member"];
export type MembershipPlan = components["schemas"]["MembershipPlan"];
export type Membership = components["schemas"]["Membership"];

export type Payment = {
  id: string;
  member: string;
  member_name: string;
  membership: string | null;
  purpose: string;
  description: string;
  amount: string;
  currency: string;
  method: string;
  business_date: string;
  received_at: string;
  status: string;
  receipt_number?: string;
};

export type DashboardData = {
  range: "today" | "month";
  income: string;
  revenue_sources: { source: string; amount: string }[];
  pending_payments: { member_count: number; amount: string };
  pending_approvals: number;
  expiring_members: number;
  attendance: { available: boolean; today: number | null };
  currency: string;
};

export type ApiEnvelope<T> = {
  success: true;
  data: T;
  message?: string;
};

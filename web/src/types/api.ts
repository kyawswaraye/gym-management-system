import type { components, operations } from "@/types/api.generated";

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
export type MemberCreateRequest = components["schemas"]["MemberCreateRequest"];
export type MemberUpdateRequest =
  components["schemas"]["PatchedMemberUpdateRequest"];
export type MemberAddressRequest =
  components["schemas"]["MemberAddressRequest"];
export type Gender = components["schemas"]["GenderEnum"];
export type MemberSummary = components["schemas"]["MemberSummary"];
export type UploadIntentRequest = components["schemas"]["UploadIntentRequest"];
export type UploadIntentData = components["schemas"]["UploadIntentData"];
export type CompleteUploadRequest =
  components["schemas"]["CompleteUploadRequest"];
export type CompletedFile = components["schemas"]["CompletedFile"];
export type DownloadUrl = components["schemas"]["DownloadUrl"];
export type DeletedFile = components["schemas"]["DeletedFile"];
export type ProfilePhotoContentType = "image/jpeg" | "image/png" | "image/webp";
export type ProfilePhotoDownload = {
  url: string;
  contentType: ProfilePhotoContentType;
};
export type CheckInChallengeIssued = {
  id: string;
  payload: string;
  display_location: string;
  issued_at: string;
  expires_at: string;
  max_uses: number;
  status: string;
};
export type CheckInRecord = components["schemas"]["CheckIn"];
export type CheckInListMeta = components["schemas"]["CheckInCollectionMeta"];
export type MemberPaymentHistoryItem =
  components["schemas"]["MemberPaymentHistoryItem"];
export type MemberPaymentHistoryMeta =
  components["schemas"]["MemberPaymentHistoryMeta"];
export type MembershipPlan = components["schemas"]["MembershipPlan"];
export type MembershipPlanUpdate =
  components["schemas"]["PatchedMembershipPlanUpdateRequest"];
export type Membership = components["schemas"]["Membership"];
export type MembershipCreateRequest =
  components["schemas"]["MembershipCreateRequest"];
export type MembershipUpdate =
  components["schemas"]["PatchedMembershipUpdateRequest"];
export type MemberListQuery = NonNullable<
  operations["members_list"]["parameters"]["query"]
>;
export type MemberListPage = {
  data: components["schemas"]["MemberListEnvelope"]["data"];
  meta: components["schemas"]["MemberListEnvelope"]["meta"];
};
export type MembershipPlanListQuery = NonNullable<
  operations["membership_plans_list"]["parameters"]["query"]
>;
export type MembershipPlanListPage = {
  data: components["schemas"]["MembershipPlanListEnvelope"]["data"];
  meta: components["schemas"]["MembershipPlanListEnvelope"]["meta"];
};
export type MembershipListQuery = NonNullable<
  operations["memberships_list"]["parameters"]["query"]
>;
export type MembershipListPage = {
  data: components["schemas"]["MembershipListEnvelope"]["data"];
  meta: components["schemas"]["MembershipListEnvelope"]["meta"];
};
export type MembershipExpiryQuery =
  operations["memberships_expiry"]["parameters"]["query"];

export type Payment = components["schemas"]["Payment"];
export type PaymentWriteRequest = components["schemas"]["PaymentWriteRequest"];
export type PaymentUpdateRequest =
  components["schemas"]["PatchedPaymentUpdateRequest"];
export type PaymentApproval = components["schemas"]["PaymentApproval"];
export type PaymentCorrection = components["schemas"]["PaymentCorrection"];
export type ApprovePaymentRequest =
  components["schemas"]["ApprovePaymentRequest"];
export type RefundPaymentRequest =
  components["schemas"]["RefundPaymentRequest"];
export type VoidPaymentRequest = components["schemas"]["VoidPaymentRequest"];
export type VoidReceiptRequest = components["schemas"]["VoidReceiptRequest"];
export type RejectPaymentRequest =
  components["schemas"]["RejectPaymentRequest"];
export type CancelPaymentRequest =
  components["schemas"]["CancelPaymentRequest"];
export type PaymentMethod = components["schemas"]["PaymentMethodEnum"];
export type PaymentPurpose = components["schemas"]["PurposeEnum"];
export type Receipt = components["schemas"]["Receipt"];
export type CashSession = components["schemas"]["CashSession"];
export type CashTransaction = components["schemas"]["CashTransaction"];
export type CashCommandData = components["schemas"]["CashCommandData"];
export type OpenCashSessionRequest =
  components["schemas"]["OpenCashSessionRequest"];
export type AdjustCashSessionRequest =
  components["schemas"]["AdjustCashSessionRequest"];
export type ReconcileCashSessionRequest =
  components["schemas"]["ReconcileCashSessionRequest"];
export type Expense = components["schemas"]["Expense"];
export type ExpenseCategory = components["schemas"]["ExpenseCategory"];
export type ExpenseWriteRequest = components["schemas"]["ExpenseWriteRequest"];
export type ExpenseUpdateRequest =
  components["schemas"]["PatchedExpenseUpdateRequest"];
export type ExpenseCategoryWriteRequest =
  components["schemas"]["ExpenseCategoryWriteRequest"];
export type ExpenseCategoryUpdateRequest =
  components["schemas"]["PatchedExpenseCategoryUpdateRequest"];
export type ExpenseApproval = components["schemas"]["ExpenseApproval"];
export type ExpenseVoid = components["schemas"]["ExpenseVoid"];
export type ApproveExpenseRequest =
  components["schemas"]["ApproveExpenseRequest"];
export type VoidExpenseRequest = components["schemas"]["VoidExpenseRequest"];
export type SalaryPeriod = components["schemas"]["SalaryPeriod"];
export type SalaryPeriodWriteRequest =
  components["schemas"]["SalaryPeriodWriteRequest"];
export type SalaryPeriodUpdateRequest =
  components["schemas"]["PatchedSalaryPeriodUpdateRequest"];
export type SalaryLineWriteRequest =
  components["schemas"]["SalaryLineWriteRequest"];
export type SalaryLinesReplaceRequest =
  components["schemas"]["SalaryLinesReplaceRequest"];
export type SalaryApproval = components["schemas"]["SalaryApproval"];
export type SalaryVoid = components["schemas"]["SalaryVoid"];
export type ApproveSalaryRequest =
  components["schemas"]["ApproveSalaryRequest"];
export type VoidSalaryRequest = components["schemas"]["VoidSalaryRequest"];
export type StaffSalaryLine = components["schemas"]["StaffSalaryLine"];
export type Product = components["schemas"]["Product"];
export type ProductCategory = components["schemas"]["ProductCategory"];
export type ProductWriteRequest = components["schemas"]["ProductWriteRequest"];
export type ProductUpdateRequest =
  components["schemas"]["PatchedProductUpdateRequest"];
export type ProductCategoryWriteRequest =
  components["schemas"]["ProductCategoryWriteRequest"];
export type StockMovement = components["schemas"]["StockMovement"];
export type StockAdjustRequest = components["schemas"]["StockAdjustRequest"];
export type StockAdjustment = components["schemas"]["StockAdjustment"];
export type MovementType = components["schemas"]["MovementTypeEnum"];
export type RetailSale = components["schemas"]["RetailSale"];
export type RetailSaleWriteRequest =
  components["schemas"]["RetailSaleWriteRequest"];
export type RetailSaleUpdateRequest =
  components["schemas"]["PatchedRetailSaleUpdateRequest"];
export type RetailSaleLineWriteRequest =
  components["schemas"]["RetailSaleLineWriteRequest"];
export type RetailSaleApproval = components["schemas"]["RetailSaleApproval"];
export type RetailSaleVoid = components["schemas"]["RetailSaleVoid"];
export type ApproveRetailSaleRequest =
  components["schemas"]["ApproveRetailSaleRequest"];
export type VoidRetailSaleRequest =
  components["schemas"]["VoidRetailSaleRequest"];
export type Direction = components["schemas"]["DirectionEnum"];

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

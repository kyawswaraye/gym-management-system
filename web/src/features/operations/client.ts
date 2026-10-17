import { getCsrfToken, mutationHeaders } from "@/features/auth/client";
import {
  buildTenantAuditListPath,
  redactTenantAuditValue,
  type TenantAuditFilters,
} from "@/features/operations/audit-policy";
import { assignableRoles } from "@/features/operations/role-policy";
import type {
  ApiEnvelope,
  CollectionMeta,
  GymProfile,
  GymProfileUpdate,
  GymSettings,
  GymSettingsUpdate,
  Member,
  MemberListPage,
  MemberListQuery,
  MemberPaymentHistoryItem,
  MemberPaymentHistoryMeta,
  Membership,
  MembershipExpiryQuery,
  MembershipListPage,
  MembershipListQuery,
  MembershipPlan,
  MembershipPlanListPage,
  MembershipPlanListQuery,
  MembershipRole,
  Permission,
  Role,
  RoleCreate,
  RoleUpdate,
  StaffInvitation,
  StaffInviteInput,
  StaffListMeta,
  StaffRecord,
  StaffUpdate,
  TenantAuditItem,
  TenantAuditMeta,
} from "@/types/api";

export class OperationError extends Error {
  readonly fields: Record<string, string>;

  constructor(message: string, fields: Record<string, string> = {}) {
    super(message);
    this.name = "OperationError";
    this.fields = fields;
  }
}

export type StaffListQuery = {
  status?: string;
  membership_status?: string;
  search?: string;
  limit?: number;
  after?: string;
};

export type StaffListPage = {
  data: StaffRecord[];
  meta: StaffListMeta;
};

export type MemberPaymentHistoryPage = {
  data: MemberPaymentHistoryItem[];
  meta: MemberPaymentHistoryMeta;
};

const PAYMENT_HISTORY_CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const PAYMENT_HISTORY_MEMBER =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function listMemberPayments(
  memberId: string,
  after?: string,
): Promise<MemberPaymentHistoryPage> {
  if (!PAYMENT_HISTORY_MEMBER.test(memberId)) {
    throw new OperationError("Member is unavailable.");
  }
  const params = new URLSearchParams();
  if (after !== undefined) {
    if (!PAYMENT_HISTORY_CURSOR.test(after)) {
      throw new OperationError("Payment history cursor is invalid.", {
        after: "Payment history cursor is invalid.",
      });
    }
    params.set("after", after);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(
    `/api/operations/members/${memberId}/payments/${suffix}`,
    { credentials: "same-origin", cache: "no-store" },
  );
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readMemberPaymentPage(body);
}

export async function operationGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api/operations/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return (body as ApiEnvelope<T>).data;
}

export async function operationMutation<T>(
  path: string,
  body: object,
  method: "POST" | "PATCH" | "PUT" = "POST",
): Promise<T> {
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/${path}`, {
    method,
    credentials: "same-origin",
    headers: mutationHeaders(csrf),
    body: JSON.stringify(body),
  });
  const responseBody: unknown = await response.json();
  if (!response.ok) throw readOperationError(responseBody);
  return (responseBody as ApiEnvelope<T>).data;
}

export function getGymProfile(): Promise<GymProfile> {
  return operationGet<GymProfile>("gym");
}

export function updateGymProfile(body: GymProfileUpdate): Promise<GymProfile> {
  return operationMutation<GymProfile>("gym", body, "PATCH");
}

export function getGymSettings(): Promise<GymSettings> {
  return operationGet<GymSettings>("settings");
}

export function updateGymSettings(
  body: GymSettingsUpdate,
): Promise<GymSettings> {
  return operationMutation<GymSettings>("settings", body, "PATCH");
}

export async function listStaff(
  query: StaffListQuery = {},
): Promise<StaffListPage> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.membership_status) {
    params.set("membership_status", query.membership_status);
  }
  if (query.search) params.set("search", query.search);
  if (query.limit !== undefined) {
    if (
      !Number.isInteger(query.limit) ||
      query.limit < 1 ||
      query.limit > 100
    ) {
      throw new OperationError("Limit must be from 1 to 100.", {
        limit: "Limit must be from 1 to 100.",
      });
    }
    params.set("limit", String(query.limit));
  }
  if (query.after) params.set("after", query.after);
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`/api/operations/staff${suffix}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readStaffPage(body);
}

export function getStaff(staffId: string): Promise<StaffRecord> {
  return operationGet<StaffRecord>(`staff/${staffId}`);
}

export function inviteStaff(body: StaffInviteInput): Promise<StaffInvitation> {
  return operationMutation<StaffInvitation>("staff/invitations", body);
}

export function updateStaff(
  staffId: string,
  body: StaffUpdate,
): Promise<StaffRecord> {
  return operationMutation<StaffRecord>(`staff/${staffId}`, body, "PATCH");
}

export function activateStaff(
  staffId: string,
  reason: string,
): Promise<StaffRecord> {
  return operationMutation<StaffRecord>(`staff/${staffId}/activate`, {
    reason,
  });
}

export function suspendStaff(
  staffId: string,
  reason: string,
): Promise<StaffRecord> {
  return operationMutation<StaffRecord>(`staff/${staffId}/suspend`, {
    reason,
  });
}

export type CollectionQuery = {
  limit?: number;
  after?: string;
};

export type CollectionPage<T> = {
  data: T[];
  meta: CollectionMeta;
};

const COLLECTION_PAGE_LIMIT = 100;
const COLLECTION_PAGE_CAP = 20;
const RESOURCE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function assertResourceId(value: string, label: string): string {
  const id = value.toLowerCase();
  if (!RESOURCE_ID.test(id)) {
    throw new OperationError(`${label} was not found.`);
  }
  return id;
}

const OPAQUE_CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

const MEMBER_STATUSES = Object.values({
  ACTIVE: "ACTIVE",
  EXPIRED: "EXPIRED",
  SUSPENDED: "SUSPENDED",
  CANCELLED: "CANCELLED",
  ARCHIVED: "ARCHIVED",
} as const satisfies Record<
  NonNullable<MemberListQuery["status"]>,
  NonNullable<MemberListQuery["status"]>
>);
const MEMBERSHIP_STATUSES = Object.values({
  PENDING: "PENDING",
  ACTIVE: "ACTIVE",
  EXPIRED: "EXPIRED",
  SUSPENDED: "SUSPENDED",
  CANCELLED: "CANCELLED",
} as const satisfies Record<
  NonNullable<MembershipListQuery["status"]>,
  NonNullable<MembershipListQuery["status"]>
>);
const MEMBERSHIP_PAYMENT_STATUSES = Object.values({
  UNPAID: "UNPAID",
  PARTIAL: "PARTIAL",
  PAID: "PAID",
  PARTIALLY_REFUNDED: "PARTIALLY_REFUNDED",
  REFUNDED: "REFUNDED",
} as const satisfies Record<
  NonNullable<MembershipListQuery["payment_status"]>,
  NonNullable<MembershipListQuery["payment_status"]>
>);
const EXPIRY_BUCKETS = Object.values({
  expired: "expired",
  today: "today",
  within_3_days: "within_3_days",
  within_7_days: "within_7_days",
  within_30_days: "within_30_days",
} as const satisfies Record<
  MembershipExpiryQuery["bucket"],
  MembershipExpiryQuery["bucket"]
>);

function assertLimit(limit: number | undefined): void {
  if (limit === undefined) return;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new OperationError("Limit must be from 1 to 100.", {
      limit: "Limit must be from 1 to 100.",
    });
  }
}

function assertCursor(after: string | undefined): void {
  if (after === undefined) return;
  if (!OPAQUE_CURSOR.test(after)) {
    throw new OperationError("Invalid cursor.", {
      after: "Invalid cursor.",
    });
  }
}

function isOneOf<T extends string>(
  value: string,
  allowed: readonly T[],
): value is T {
  return (allowed as readonly string[]).includes(value);
}

function assertEnum<T extends string>(
  value: string | undefined,
  allowed: readonly T[],
  field: string,
  message: string,
): T | undefined {
  if (value === undefined) return undefined;
  if (!isOneOf(value, allowed)) {
    throw new OperationError(message, { [field]: message });
  }
  return value;
}

function assertSearch(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const trimmed = value.trim();
  if (trimmed.length === 0 || trimmed.length > 100) {
    throw new OperationError("Search must be from 1 to 100 characters.", {
      search: "Search must be from 1 to 100 characters.",
    });
  }
  return trimmed;
}

function isIsoDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function assertIsoDate(
  value: string | undefined,
  field: string,
): string | undefined {
  if (value === undefined) return undefined;
  if (!isIsoDate(value)) {
    throw new OperationError("Enter a valid date.", {
      [field]: "Enter a valid date.",
    });
  }
  return value;
}

function collectionParams(query: CollectionQuery): URLSearchParams {
  assertLimit(query.limit);
  assertCursor(query.after);
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.after !== undefined) params.set("after", query.after);
  return params;
}

async function collectionGet<T>(
  path: string,
  params: URLSearchParams,
): Promise<CollectionPage<T>> {
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`/api/operations/${path}${suffix}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readCollection<T>(body);
}

async function listAllCollection<T extends { id: string }>(
  load: (query: CollectionQuery) => Promise<CollectionPage<T>>,
  label: string,
): Promise<T[]> {
  const items: T[] = [];
  const seenIds = new Set<string>();
  const seenCursors = new Set<string>();
  let after: string | undefined;
  for (let page = 0; page < COLLECTION_PAGE_CAP; page += 1) {
    const result = await load({
      limit: COLLECTION_PAGE_LIMIT,
      ...(after ? { after } : {}),
    });
    for (const item of result.data) {
      if (seenIds.has(item.id)) continue;
      seenIds.add(item.id);
      items.push(item);
    }
    const next = result.meta.next_after;
    if (!result.meta.has_more) return items;
    if (!next || seenCursors.has(next)) {
      throw new OperationError(`The ${label} list could not be loaded.`);
    }
    seenCursors.add(next);
    after = next;
  }
  throw new OperationError(`The ${label} list is too large to load.`);
}

export async function listMembers(
  query: MemberListQuery = {},
): Promise<MemberListPage> {
  const status = assertEnum(
    query.status,
    MEMBER_STATUSES,
    "status",
    "Status is invalid.",
  );
  const search = assertSearch(query.search);
  const joinedFrom = assertIsoDate(query.joined_from, "joined_from");
  const joinedTo = assertIsoDate(query.joined_to, "joined_to");
  if (joinedFrom && joinedTo && joinedFrom > joinedTo) {
    throw new OperationError("Joined to must be on or after joined from.", {
      joined_to: "Joined to must be on or after joined from.",
    });
  }
  if (
    query.is_pt_client !== undefined &&
    query.is_pt_client !== null &&
    typeof query.is_pt_client !== "boolean"
  ) {
    throw new OperationError("PT client filter is invalid.", {
      is_pt_client: "PT client filter is invalid.",
    });
  }
  assertLimit(query.limit);
  assertCursor(query.after);
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (joinedFrom) params.set("joined_from", joinedFrom);
  if (joinedTo) params.set("joined_to", joinedTo);
  if (typeof query.is_pt_client === "boolean") {
    params.set("is_pt_client", query.is_pt_client ? "true" : "false");
  }
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.after !== undefined) params.set("after", query.after);
  return collectionGet<Member>("members", params);
}

export async function listMembershipPlans(
  query: MembershipPlanListQuery = {},
): Promise<MembershipPlanListPage> {
  return collectionGet<MembershipPlan>(
    "membership-plans",
    collectionParams(query),
  );
}

export function listAllMembershipPlans(): Promise<MembershipPlan[]> {
  return listAllCollection(listMembershipPlans, "membership plan");
}

export async function listMemberships(
  query: MembershipListQuery = {},
): Promise<MembershipListPage> {
  const status = assertEnum(
    query.status,
    MEMBERSHIP_STATUSES,
    "status",
    "Status is invalid.",
  );
  const paymentStatus = assertEnum(
    query.payment_status,
    MEMBERSHIP_PAYMENT_STATUSES,
    "payment_status",
    "Payment status is invalid.",
  );
  let memberId: string | undefined;
  if (query.member_id !== undefined) {
    memberId = query.member_id.toLowerCase();
    if (!RESOURCE_ID.test(memberId)) {
      throw new OperationError("Enter a valid member id.", {
        member_id: "Enter a valid member id.",
      });
    }
  }
  assertLimit(query.limit);
  assertCursor(query.after);
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (memberId) params.set("member_id", memberId);
  if (paymentStatus) params.set("payment_status", paymentStatus);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.after !== undefined) params.set("after", query.after);
  return collectionGet<Membership>("memberships", params);
}

export async function listMembershipExpiry(
  query: MembershipExpiryQuery,
): Promise<MembershipListPage> {
  const bucket = assertEnum(
    query.bucket,
    EXPIRY_BUCKETS,
    "bucket",
    "Expiry bucket is invalid.",
  );
  if (!bucket) {
    throw new OperationError("Expiry bucket is invalid.", {
      bucket: "Expiry bucket is invalid.",
    });
  }
  assertLimit(query.limit);
  assertCursor(query.after);
  const params = new URLSearchParams();
  params.set("bucket", bucket);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.after !== undefined) params.set("after", query.after);
  return collectionGet<Membership>("memberships/expiry", params);
}

export async function listRoles(
  query: CollectionQuery = {},
): Promise<CollectionPage<Role>> {
  return collectionGet<Role>("roles", collectionParams(query));
}

export function listAllRoles(): Promise<Role[]> {
  return listAllCollection(listRoles, "role");
}

export async function listActiveRoleCatalog(): Promise<Role[]> {
  const roles = await listAllRoles();
  return roles.filter((role) => role.is_active && role.key !== "OWNER");
}

export async function listAssignableRoles(input: {
  isOwner: boolean;
  livePermissions: readonly string[];
}): Promise<Role[]> {
  const catalog = await listActiveRoleCatalog();
  return assignableRoles(catalog, input);
}

export async function getRole(roleId: string): Promise<Role> {
  return operationGet<Role>(`roles/${assertResourceId(roleId, "Role")}`);
}

export function createRole(body: RoleCreate): Promise<Role> {
  return operationMutation<Role>("roles", body);
}

export async function updateRole(
  roleId: string,
  body: RoleUpdate,
): Promise<Role> {
  return operationMutation<Role>(
    `roles/${assertResourceId(roleId, "Role")}`,
    body,
    "PATCH",
  );
}

export async function archiveRole(roleId: string): Promise<Role> {
  return operationMutation<Role>(
    `roles/${assertResourceId(roleId, "Role")}/archive`,
    {},
  );
}

export async function replaceRolePermissions(
  roleId: string,
  permissionCodes: readonly string[],
): Promise<Role> {
  return operationMutation<Role>(
    `roles/${assertResourceId(roleId, "Role")}/permissions`,
    { permission_codes: [...permissionCodes] },
    "PUT",
  );
}

export type TenantAuditPage = {
  data: TenantAuditItem[];
  meta: TenantAuditMeta;
};

export async function listAuditLogs(
  filters: TenantAuditFilters,
): Promise<TenantAuditPage> {
  const built = buildTenantAuditListPath(filters);
  if (!built.ok) throw new OperationError(built.message);
  const response = await fetch(`/api/operations/${built.path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readTenantAuditPage(body);
}

export async function listPermissions(
  query: CollectionQuery = {},
): Promise<CollectionPage<Permission>> {
  return collectionGet<Permission>("permissions", collectionParams(query));
}

export function listAllPermissions(): Promise<Permission[]> {
  return listAllCollection(listPermissions, "permission");
}

export async function getMembershipRoles(
  membershipId: string,
): Promise<MembershipRole> {
  return operationGet<MembershipRole>(
    `gym-memberships/${assertResourceId(membershipId, "Membership")}/roles`,
  );
}

export async function replaceMembershipRoles(
  membershipId: string,
  roleIds: readonly string[],
): Promise<MembershipRole> {
  return operationMutation<MembershipRole>(
    `gym-memberships/${assertResourceId(membershipId, "Membership")}/roles`,
    { role_ids: [...roleIds] },
    "PUT",
  );
}

function readTenantAuditPage(body: unknown): TenantAuditPage {
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new OperationError("The audit list could not be loaded.");
  }
  const envelope = body as {
    data?: unknown;
    meta?: { has_more?: unknown; next_after?: unknown; scope?: unknown };
  };
  if (!Array.isArray(envelope.data)) {
    throw new OperationError("The audit list could not be loaded.");
  }
  const scope = envelope.meta?.scope;
  if (scope !== "GYM" && scope !== "SELF") {
    throw new OperationError("The audit list could not be loaded.");
  }
  const cursor = readCursorMeta(envelope.meta);
  return {
    data: envelope.data.map(readTenantAuditItem),
    meta: {
      has_more: cursor.has_more,
      next_after: cursor.next_after,
      scope,
    },
  };
}

function readCursorMeta(
  meta:
    | {
        has_more?: unknown;
        next_after?: unknown;
      }
    | undefined,
): CollectionMeta {
  const nextAfter = meta?.next_after;
  return {
    has_more: meta?.has_more === true,
    next_after: typeof nextAfter === "string" && nextAfter ? nextAfter : null,
  };
}

function readTenantAuditItem(value: unknown): TenantAuditItem {
  if (typeof value !== "object" || value === null) {
    throw new OperationError("The audit list could not be loaded.");
  }
  const row = value as Record<string, unknown>;
  const id = row.id;
  const action = row.action;
  const moduleName = row.module;
  const targetType = row.target_type;
  const targetId = row.target_id;
  const requestId = row.request_id;
  const outcome = row.outcome;
  const occurredAt = row.occurred_at;
  const actor = row.actor_membership_id;
  const impersonator = row.impersonator_membership_id;
  if (
    typeof id !== "string" ||
    typeof action !== "string" ||
    typeof moduleName !== "string" ||
    typeof targetType !== "string" ||
    typeof targetId !== "string" ||
    typeof requestId !== "string" ||
    typeof outcome !== "string" ||
    typeof occurredAt !== "string" ||
    !(actor === null || typeof actor === "string") ||
    !(impersonator === null || typeof impersonator === "string")
  ) {
    throw new OperationError("The audit list could not be loaded.");
  }
  return {
    id,
    actor_membership_id: actor,
    impersonator_membership_id: impersonator,
    action,
    module: moduleName,
    target_type: targetType,
    target_id: targetId,
    old_values: redactTenantAuditValue(row.old_values),
    new_values: redactTenantAuditValue(row.new_values),
    request_id: requestId,
    outcome,
    occurred_at: occurredAt,
  };
}

function readCollection<T>(body: unknown): CollectionPage<T> {
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new OperationError("The request could not be completed.");
  }
  const envelope = body as {
    data?: unknown;
    meta?: { has_more?: unknown; next_after?: unknown };
  };
  if (!Array.isArray(envelope.data)) {
    throw new OperationError("The request could not be completed.");
  }
  return {
    data: envelope.data as T[],
    meta: readCursorMeta(envelope.meta),
  };
}

function readMemberPaymentPage(body: unknown): MemberPaymentHistoryPage {
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new OperationError("Unable to load payment history.");
  }
  const envelope = body as {
    data?: unknown;
    meta?: { has_more?: unknown; next_after?: unknown };
  };
  if (!Array.isArray(envelope.data)) {
    throw new OperationError("Unable to load payment history.");
  }
  return {
    data: envelope.data as MemberPaymentHistoryItem[],
    meta: readCursorMeta(envelope.meta),
  };
}

function readStaffPage(body: unknown): StaffListPage {
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new OperationError("The request could not be completed.");
  }
  const envelope = body as {
    data?: unknown;
    meta?: { has_more?: unknown; next_after?: unknown };
  };
  if (!Array.isArray(envelope.data)) {
    throw new OperationError("The request could not be completed.");
  }
  return {
    data: envelope.data as StaffRecord[],
    meta: readCursorMeta(envelope.meta),
  };
}

function fieldText(value: unknown): string | undefined {
  if (typeof value === "string" && value) return value;
  if (!Array.isArray(value)) return undefined;
  return value.find((item): item is string => typeof item === "string");
}

function readOperationError(body: unknown): OperationError {
  const fallback = "The request could not be completed.";
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return new OperationError(fallback);
  }
  const error = (body as { error?: { message?: unknown; fields?: unknown } })
    .error;
  const fields: Record<string, string> = {};
  if (error?.fields && typeof error.fields === "object") {
    for (const [key, value] of Object.entries(error.fields)) {
      const message = fieldText(value);
      if (message) fields[key] = message;
    }
  }
  const fieldMessage = Object.values(fields)[0];
  const message =
    fieldMessage ||
    (typeof error?.message === "string" && error.message
      ? error.message
      : fallback);
  return new OperationError(message, fields);
}

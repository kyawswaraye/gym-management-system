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
  MemberPaymentHistoryItem,
  MemberPaymentHistoryMeta,
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

function collectionSuffix(query: CollectionQuery): string {
  const params = new URLSearchParams();
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
  return params.size ? `?${params.toString()}` : "";
}

async function collectionGet<T>(
  path: string,
  query: CollectionQuery = {},
): Promise<CollectionPage<T>> {
  const response = await fetch(
    `/api/operations/${path}${collectionSuffix(query)}`,
    {
      credentials: "same-origin",
      cache: "no-store",
    },
  );
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

export function listRoles(
  query: CollectionQuery = {},
): Promise<CollectionPage<Role>> {
  return collectionGet<Role>("roles", query);
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

export function listPermissions(
  query: CollectionQuery = {},
): Promise<CollectionPage<Permission>> {
  return collectionGet<Permission>("permissions", query);
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
  const nextAfter = envelope.meta?.next_after;
  return {
    data: envelope.data.map(readTenantAuditItem),
    meta: {
      has_more: envelope.meta?.has_more === true,
      next_after: typeof nextAfter === "string" && nextAfter ? nextAfter : null,
      scope,
    },
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
  const nextAfter = envelope.meta?.next_after;
  return {
    data: envelope.data as T[],
    meta: {
      has_more: envelope.meta?.has_more === true,
      next_after: typeof nextAfter === "string" && nextAfter ? nextAfter : null,
    },
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
  const nextAfter = envelope.meta?.next_after;
  return {
    data: envelope.data as MemberPaymentHistoryItem[],
    meta: {
      has_more: envelope.meta?.has_more === true,
      next_after: typeof nextAfter === "string" && nextAfter ? nextAfter : null,
    },
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
  const nextAfter = envelope.meta?.next_after;
  return {
    data: envelope.data as StaffRecord[],
    meta: {
      has_more: envelope.meta?.has_more === true,
      next_after: typeof nextAfter === "string" && nextAfter ? nextAfter : null,
    },
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

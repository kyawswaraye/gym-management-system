import { getCsrfToken, mutationHeaders } from "@/features/auth/client";
import { isAllowedObjectUrl } from "@/lib/object-origin";
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
  CheckInChallengeIssued,
  CheckInRecord,
  CompleteUploadRequest,
  CompletedFile,
  DownloadUrl,
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
  ProfilePhotoContentType,
  ProfilePhotoDownload,
  UploadIntentData,
  UploadIntentRequest,
} from "@/types/api";

export class OperationError extends Error {
  readonly fields: Record<string, string>;
  readonly code: string;

  constructor(message: string, fields: Record<string, string> = {}, code = "") {
    super(message);
    this.name = "OperationError";
    this.fields = fields;
    this.code = code;
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

export async function operationList<T>(
  path: string,
): Promise<CollectionPage<T>> {
  const response = await fetch(`/api/operations/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readCollection<T>(body);
}

export async function operationMutation<T>(
  path: string,
  body: object,
  method: "POST" | "PATCH" | "PUT" = "POST",
  idempotencyKey?: string,
): Promise<T> {
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...mutationHeaders(csrf),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify(body),
  });
  const responseBody: unknown = await response.json();
  if (!response.ok) throw readOperationError(responseBody);
  return (responseBody as ApiEnvelope<T>).data;
}

export const PROFILE_PHOTO_MAX_BYTES = 5 * 1024 * 1024;

const PROFILE_PHOTO_TYPES = new Set<ProfilePhotoContentType>([
  "image/jpeg",
  "image/png",
  "image/webp",
]);

const PHOTO_NAME_FALLBACK: Record<ProfilePhotoContentType, string> = {
  "image/jpeg": "profile-photo.jpg",
  "image/png": "profile-photo.png",
  "image/webp": "profile-photo.webp",
};

export class FileTransferError extends OperationError {
  readonly recoverableFileId: string | null;
  readonly attachReady: boolean;

  constructor(
    message: string,
    recoverableFileId: string | null = null,
    attachReady = false,
  ) {
    super(message);
    this.name = "FileTransferError";
    this.recoverableFileId = recoverableFileId;
    this.attachReady = attachReady;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isProfilePhotoContentType(
  value: unknown,
): value is ProfilePhotoContentType {
  return (
    typeof value === "string" &&
    PROFILE_PHOTO_TYPES.has(value as ProfilePhotoContentType)
  );
}

function profilePhotoContentType(file: File): ProfilePhotoContentType {
  if (!isProfilePhotoContentType(file.type)) {
    throw new OperationError("Choose a JPEG, PNG, or WebP image.");
  }
  return file.type;
}

function profilePhotoByteSize(file: File): number {
  if (!Number.isInteger(file.size) || file.size < 1) {
    throw new OperationError("Choose a photo that is not empty.");
  }
  if (file.size > PROFILE_PHOTO_MAX_BYTES) {
    throw new OperationError("Choose a photo that is 5 MB or smaller.");
  }
  return file.size;
}

function originalPhotoName(
  file: File,
  contentType: ProfilePhotoContentType,
): string {
  const base = file.name.split(/[/\\]/).pop() ?? "";
  const cleaned = base.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  if (!cleaned || cleaned.length > 255) return PHOTO_NAME_FALLBACK[contentType];
  return cleaned;
}

async function sha256Hex(bytes: BufferSource): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new OperationError("Unable to upload the profile photo.");
  }
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  let hex = "";
  for (const byte of new Uint8Array(digest)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

type DirectUpload = {
  fileId: string;
  url: string;
  fields: Array<[string, string]>;
};

function transferFailure(fileId: string | null = null): never {
  throw new FileTransferError("Unable to upload the profile photo.", fileId);
}

function readDirectUpload(data: unknown, ownerId: string): DirectUpload {
  if (!isRecord(data)) transferFailure();
  const fileId =
    typeof data.file_id === "string" ? data.file_id.toLowerCase() : "";
  const recoverable = RESOURCE_ID.test(fileId) ? fileId : null;
  const owner =
    typeof data.owner_id === "string" ? data.owner_id.toLowerCase() : "";
  if (
    data.purpose !== "PROFILE_PHOTO" ||
    data.owner_type !== "MEMBER" ||
    owner !== ownerId ||
    !recoverable
  ) {
    transferFailure(recoverable);
  }
  const upload = data.upload;
  if (
    !isRecord(upload) ||
    upload.method !== "POST" ||
    upload.file_field !== "file"
  ) {
    transferFailure(recoverable);
  }
  if (typeof upload.url !== "string" || !isAllowedObjectUrl(upload.url)) {
    transferFailure(recoverable);
  }
  if (!isRecord(upload.fields)) transferFailure(recoverable);
  const entries = Object.entries(upload.fields);
  if (
    entries.some(
      ([key, fieldValue]) =>
        key.length === 0 || key === "file" || typeof fieldValue !== "string",
    )
  ) {
    transferFailure(recoverable);
  }
  const fields = entries as Array<[string, string]>;
  if (fields.find(([key]) => key === "success_action_status")?.[1] !== "204") {
    transferFailure(recoverable);
  }
  return { fileId: recoverable, url: upload.url, fields };
}

async function postProfileObject(
  target: DirectUpload,
  file: File,
): Promise<void> {
  const form = new FormData();
  for (const [key, fieldValue] of target.fields) {
    form.append(key, fieldValue);
  }
  form.append("file", file);
  let response: Response;
  try {
    response = await fetch(target.url, {
      method: "POST",
      body: form,
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      mode: "cors",
    });
  } catch {
    throw new FileTransferError(
      "The file service is unavailable.",
      target.fileId,
    );
  }
  if (response.status !== 204) {
    throw new FileTransferError(
      "The file service did not accept the profile photo.",
      target.fileId,
    );
  }
}

function readCompletedFile(
  data: unknown,
  ownerId: string,
  fileId: string,
): string {
  if (!isRecord(data)) transferFailure(fileId);
  const completedId =
    typeof data.file_id === "string" ? data.file_id.toLowerCase() : "";
  const owner =
    typeof data.owner_id === "string" ? data.owner_id.toLowerCase() : "";
  if (
    completedId !== fileId ||
    data.purpose !== "PROFILE_PHOTO" ||
    data.owner_type !== "MEMBER" ||
    owner !== ownerId ||
    data.attachable !== true
  ) {
    transferFailure(fileId);
  }
  return fileId;
}

function withRecoverableFile(
  caught: unknown,
  fileId: string,
  fallback: string,
): FileTransferError {
  if (caught instanceof FileTransferError) return caught;
  const message =
    caught instanceof OperationError && caught.message
      ? caught.message
      : fallback;
  return new FileTransferError(message, fileId);
}

export async function uploadProfilePhoto(
  memberId: string,
  file: File,
): Promise<{ fileId: string }> {
  const ownerId = assertResourceId(memberId, "Member");
  const contentType = profilePhotoContentType(file);
  const byteSize = profilePhotoByteSize(file);
  const bytes = Uint8Array.from(new Uint8Array(await file.arrayBuffer()));
  if (bytes.byteLength !== byteSize) {
    throw new OperationError("Choose a photo that is 5 MB or smaller.");
  }
  const sha256 = await sha256Hex(bytes);
  const request: UploadIntentRequest = {
    original_name: originalPhotoName(file, contentType),
    content_type: contentType,
    byte_size: byteSize,
    sha256,
    purpose: "PROFILE_PHOTO",
    owner_type: "MEMBER",
    owner_id: ownerId,
  };
  let intent: UploadIntentData;
  try {
    intent = await operationMutation<UploadIntentData>(
      "files/upload-intents/",
      request,
    );
  } catch (caught) {
    if (caught instanceof OperationError) throw caught;
    throw new OperationError("Unable to upload the profile photo.");
  }
  const target = readDirectUpload(intent, ownerId);
  try {
    await postProfileObject(target, file);
  } catch (caught) {
    throw withRecoverableFile(
      caught,
      target.fileId,
      "The file service is unavailable.",
    );
  }
  let completed: CompletedFile;
  try {
    const confirmation: CompleteUploadRequest = {
      sha256,
      byte_size: byteSize,
    };
    completed = await operationMutation<CompletedFile>(
      `files/${target.fileId}/complete/`,
      confirmation,
    );
  } catch (caught) {
    throw withRecoverableFile(
      caught,
      target.fileId,
      "Unable to upload the profile photo.",
    );
  }
  return { fileId: readCompletedFile(completed, ownerId, target.fileId) };
}

function readProfilePhotoDownload(data: unknown): ProfilePhotoDownload {
  if (
    !isRecord(data) ||
    data.method !== "GET" ||
    typeof data.url !== "string" ||
    !isProfilePhotoContentType(data.content_type) ||
    !isAllowedObjectUrl(data.url)
  ) {
    throw new OperationError("Unable to load the profile photo.");
  }
  return { url: data.url, contentType: data.content_type };
}

export async function getFileDownloadUrl(
  fileId: string,
): Promise<ProfilePhotoDownload> {
  const id = assertResourceId(fileId, "File");
  const data = await operationGet<DownloadUrl>(`files/${id}/download-url/`);
  return readProfilePhotoDownload(data);
}

export async function deleteFileAsset(fileId: string): Promise<void> {
  const id = assertResourceId(fileId, "File");
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/files/${id}/`, {
    method: "DELETE",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "X-CSRF-Token": csrf },
  });
  if (response.ok) return;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  throw readOperationError(body);
}

export function getGymProfile(): Promise<GymProfile> {
  return operationGet<GymProfile>("gym");
}

export function updateGymProfile(body: GymProfileUpdate): Promise<GymProfile> {
  return operationMutation<GymProfile>("gym", body, "PATCH");
}

const GYM_LOGO_MAX_BYTES = 1_048_576;

function notifyGymLogoChanged(): void {
  window.dispatchEvent(new Event("gym-logo-updated"));
}

async function normalizedLogoFile(file: File): Promise<File> {
  if (file.size < 1 || file.size > GYM_LOGO_MAX_BYTES) {
    throw new OperationError(
      "The logo must be a JPEG, PNG, or WebP under 1 MB.",
    );
  }
  const header = new Uint8Array(await file.slice(0, 16).arrayBuffer());
  const jpeg = header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff;
  const png =
    header[0] === 0x89 &&
    header[1] === 0x50 &&
    header[2] === 0x4e &&
    header[3] === 0x47;
  const webp =
    header[0] === 0x52 &&
    header[1] === 0x49 &&
    header[2] === 0x46 &&
    header[3] === 0x46 &&
    header[8] === 0x57 &&
    header[9] === 0x45 &&
    header[10] === 0x42 &&
    header[11] === 0x50;
  const type = jpeg
    ? "image/jpeg"
    : png
      ? "image/png"
      : webp
        ? "image/webp"
        : "";
  if (!type) {
    throw new OperationError(
      "The logo must be a JPEG, PNG, or WebP under 1 MB.",
    );
  }
  if (file.type === type) return file;
  return new File([file], file.name || "logo", { type });
}

export async function uploadGymLogo(file: File): Promise<GymProfile> {
  const logo = await normalizedLogoFile(file);
  const csrf = await getCsrfToken();
  const body = new FormData();
  body.set("logo", logo);
  const response = await fetch("/api/operations/gym/logo", {
    method: "PUT",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf },
    body,
  });
  const responseBody: unknown = await response.json();
  if (!response.ok) throw readOperationError(responseBody);
  notifyGymLogoChanged();
  return (responseBody as ApiEnvelope<GymProfile>).data;
}

export async function deleteGymLogo(): Promise<GymProfile> {
  const csrf = await getCsrfToken();
  const response = await fetch("/api/operations/gym/logo", {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf },
  });
  const responseBody: unknown = await response.json();
  if (!response.ok) throw readOperationError(responseBody);
  notifyGymLogoChanged();
  return (responseBody as ApiEnvelope<GymProfile>).data;
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

const CHECK_IN_IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{16,128}$/;
const CHECK_IN_NOTE_LIMIT = 500;
const AWARE_INSTANT =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;

export type CheckInListQuery = {
  memberId?: string;
  open?: "true" | "false";
  limit?: number;
  after?: string;
};

export async function listCheckIns(
  query: CheckInListQuery = {},
): Promise<CollectionPage<CheckInRecord>> {
  let memberId: string | undefined;
  if (query.memberId !== undefined) {
    memberId = query.memberId.toLowerCase();
    if (!RESOURCE_ID.test(memberId)) {
      throw new OperationError("Enter a valid member id.", {
        member_id: "Enter a valid member id.",
      });
    }
  }
  if (
    query.open !== undefined &&
    query.open !== "true" &&
    query.open !== "false"
  ) {
    throw new OperationError("Open filter is invalid.", {
      open: "Open filter is invalid.",
    });
  }
  assertLimit(query.limit);
  assertCursor(query.after);
  const params = new URLSearchParams();
  if (memberId) params.set("member_id", memberId);
  if (query.open) params.set("open", query.open);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.after !== undefined) params.set("after", query.after);
  return collectionGet<CheckInRecord>("check-ins", params);
}

export async function listMemberCheckIns(
  memberId: string,
): Promise<CollectionPage<CheckInRecord>> {
  const id = assertResourceId(memberId, "Member");
  const response = await fetch(
    `/api/operations/members/${id}/check-ins/?limit=10`,
    { credentials: "same-origin", cache: "no-store" },
  );
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return readCollection<CheckInRecord>(body);
}

function assertCheckInInstant(value: string | undefined): string | undefined {
  if (value === undefined || value === "") return undefined;
  if (!AWARE_INSTANT.test(value)) {
    throw new OperationError("Enter a timezone-aware time.", {
      occurred_at: "Enter a timezone-aware time.",
    });
  }
  return value;
}

export async function createCheckIn(input: {
  memberId: string;
  note?: string;
  occurredAt?: string;
  idempotencyKey: string;
}): Promise<CheckInRecord> {
  const memberId = assertResourceId(input.memberId, "Member");
  const note = input.note?.trim() ?? "";
  if (note.length > CHECK_IN_NOTE_LIMIT) {
    throw new OperationError("Note is too long.", {
      note: "Use 500 characters or fewer.",
    });
  }
  if (!CHECK_IN_IDEMPOTENCY_KEY.test(input.idempotencyKey)) {
    throw new OperationError("The check-in could not be sent.");
  }
  const occurredAt = assertCheckInInstant(input.occurredAt);
  const payload: { member_id: string; note?: string; occurred_at?: string } = {
    member_id: memberId,
  };
  if (note) payload.note = note;
  if (occurredAt) payload.occurred_at = occurredAt;
  const csrf = await getCsrfToken();
  const response = await fetch("/api/operations/check-ins/", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      ...mutationHeaders(csrf),
      "Idempotency-Key": input.idempotencyKey,
    },
    body: JSON.stringify(payload),
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return (body as ApiEnvelope<CheckInRecord>).data;
}

export async function scanTrainerCheckIn(input: {
  memberId: string;
  payload: string;
}): Promise<CheckInRecord> {
  const memberId = assertResourceId(input.memberId, "Member");
  if (input.payload.length === 0 || input.payload.length > 128) {
    throw new OperationError("The check-in code is not valid.");
  }
  const csrf = await getCsrfToken();
  const response = await fetch("/api/operations/trainer/check-ins/scan/", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: mutationHeaders(csrf),
    body: JSON.stringify({ member_id: memberId, payload: input.payload }),
  });
  const body: unknown = await response.json();
  if (!response.ok) throw readOperationError(body);
  return (body as ApiEnvelope<CheckInRecord>).data;
}

export async function checkOutCheckIn(
  checkInId: string,
  occurredAt?: string,
): Promise<CheckInRecord> {
  const id = assertResourceId(checkInId, "Check-in");
  const instant = assertCheckInInstant(occurredAt);
  return operationMutation<CheckInRecord>(
    `check-ins/${id}/check-out`,
    instant ? { occurred_at: instant } : {},
  );
}

export async function issueCheckInChallenge(
  displayLocation: string,
): Promise<CheckInChallengeIssued> {
  const location = displayLocation.trim().replace(/\s+/g, " ");
  if (
    location.length < 1 ||
    location.length > 80 ||
    /[\u0000-\u001f\u007f]/.test(location)
  ) {
    throw new OperationError(
      "Enter a display location of 1 to 80 characters.",
      {
        display_location: "Enter a display location of 1 to 80 characters.",
      },
    );
  }
  return operationMutation<CheckInChallengeIssued>("check-in-challenges", {
    display_location: location,
  });
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
  const error = (
    body as { error?: { code?: unknown; message?: unknown; fields?: unknown } }
  ).error;
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
  const code = typeof error?.code === "string" ? error.code : "";
  return new OperationError(message, fields, code);
}

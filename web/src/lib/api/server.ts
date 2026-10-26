import "server-only";

import { AsyncLocalStorage } from "node:async_hooks";
import { headers as nextHeaders } from "next/headers";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { redactTenantAuditValue } from "@/features/operations/audit-policy";
import {
  allowsFinanceRoute,
  FINANCE_PATH_PATTERNS,
} from "@/lib/api/finance-routes";
import {
  allowsPhase5Route,
  PHASE5_PATH_PATTERNS,
  phase5Idempotency,
} from "@/lib/api/phase5-routes";
import {
  allowsReportRoute,
  REPORT_PATH_PATTERNS,
} from "@/lib/api/reports-routes";
import {
  AUTH_COOKIES,
  SELECTION_COOKIE_MAX_AGE,
  authCookieOptions,
  clearCookieOptions,
} from "@/lib/auth/cookies";
import { verifyMutationRequest } from "@/lib/auth/csrf";
import { getServerEnv } from "@/lib/env";
import {
  logBffFailure,
  normalizedRoute,
  resolveRequestId,
} from "@/lib/log/structured";
import type { ApiErrorBody } from "@/types/api";

const ALLOWED_PATHS = new Set([
  "/api/v1/auth/login/",
  "/api/v1/auth/refresh/",
  "/api/v1/auth/logout/",
  "/api/v1/auth/logout-all/",
  "/api/v1/auth/me/",
  "/api/v1/auth/gyms/",
  "/api/v1/auth/select-gym/",
  "/api/v1/auth/mfa/enroll/",
  "/api/v1/auth/mfa/enroll/confirm/",
  "/api/v1/auth/mfa/challenge/",
  "/api/v1/owners/invitations/accept/",
  "/api/v1/staff/invitations/accept/",
  "/api/v1/auth/password/forgot/",
  "/api/v1/auth/password/reset/",
]);

const MEMBER_PAYMENT_HISTORY_PATH =
  /^\/api\/v1\/members\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/payments\/$/;
const MEMBER_CHECK_IN_HISTORY_PATH =
  /^\/api\/v1\/members\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/check-ins\/$/;
const CHECK_IN_LIST_PATH = "/api/v1/check-ins/";
const CHECK_IN_LIST_PATTERN = /^\/api\/v1\/check-ins\/$/;
const CHECK_IN_DETAIL_PATH =
  /^\/api\/v1\/check-ins\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/$/;
const CHECK_IN_CHECKOUT_PATH =
  /^\/api\/v1\/check-ins\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/check-out\/$/;
const CHECK_IN_CHALLENGE_PATH = "/api/v1/check-in-challenges/";
const CHECK_IN_CHALLENGE_PATTERN = /^\/api\/v1\/check-in-challenges\/$/;
const FILE_UPLOAD_INTENT_PATH = "/api/v1/files/upload-intents/";
const FILE_UPLOAD_INTENT_PATTERN = /^\/api\/v1\/files\/upload-intents\/$/;
const FILE_ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const FILE_COMPLETE_PATH = new RegExp(`^/api/v1/files/${FILE_ID}/complete/$`);
const FILE_DOWNLOAD_PATH = new RegExp(
  `^/api/v1/files/${FILE_ID}/download-url/$`,
);
const FILE_DELETE_PATH = new RegExp(`^/api/v1/files/${FILE_ID}/$`);

const ALLOWED_TENANT_PATHS = [
  /^\/api\/v1\/gym\/$/,
  /^\/api\/v1\/gym\/logo\/$/,
  /^\/api\/v1\/settings\/$/,
  /^\/api\/v1\/owners\/$/,
  /^\/api\/v1\/owners\/invitations\/$/,
  /^\/api\/v1\/owners\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/cancel-invitation\/$/,
  /^\/api\/v1\/owners\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/revoke\/$/,
  /^\/api\/v1\/staff\/$/,
  /^\/api\/v1\/staff\/invitations\/$/,
  /^\/api\/v1\/staff\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/$/,
  /^\/api\/v1\/staff\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/activate\/$/,
  /^\/api\/v1\/staff\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/suspend\/$/,
  /^\/api\/v1\/roles\/$/,
  /^\/api\/v1\/roles\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/$/,
  /^\/api\/v1\/roles\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/archive\/$/,
  /^\/api\/v1\/roles\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/permissions\/$/,
  /^\/api\/v1\/permissions\/$/,
  /^\/api\/v1\/gym-memberships\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/roles\/$/,
  /^\/api\/v1\/members\/(?:[0-9a-f-]+\/(?:summary\/|archive\/)?)?$/,
  MEMBER_PAYMENT_HISTORY_PATH,
  MEMBER_CHECK_IN_HISTORY_PATH,
  CHECK_IN_LIST_PATTERN,
  CHECK_IN_DETAIL_PATH,
  CHECK_IN_CHECKOUT_PATH,
  CHECK_IN_CHALLENGE_PATTERN,
  /^\/api\/v1\/membership-plans\/(?:[0-9a-f-]+\/(?:archive\/)?)?$/,
  /^\/api\/v1\/memberships\/(?:expiry\/|[0-9a-f-]+\/(?:cancel\/|suspend\/|resume\/)?)?$/,
  ...FINANCE_PATH_PATTERNS,
  ...PHASE5_PATH_PATTERNS,
  /^\/api\/v1\/dashboard\/$/,
  ...REPORT_PATH_PATTERNS,
  /^\/api\/v1\/audit-logs\/$/,
  FILE_UPLOAD_INTENT_PATTERN,
  FILE_COMPLETE_PATH,
  FILE_DOWNLOAD_PATH,
  FILE_DELETE_PATH,
];

const PLATFORM_DASHBOARD_PATH = /^\/api\/v1\/admin\/dashboard\/$/;
const PLATFORM_SUBSCRIPTION_LIST_PATH = "/api/v1/admin/subscriptions/";
const PLATFORM_AUDIT_LIST_PATH = "/api/v1/admin/audit-logs/";
const PLATFORM_SUBSCRIPTION_DETAIL_PATH =
  /^\/api\/v1\/admin\/subscriptions\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/$/;
const SUBSCRIPTION_LIST_STATUS = new Set(["ACTIVE", "RETIRED", "ALL"]);
const AUDIT_OUTCOMES = new Set(["SUCCESS", "DENIED", "ERROR"]);
const AUDIT_FILTERS = new Set([
  "target_gym_id",
  "action",
  "outcome",
  "limit",
  "after",
]);
const CANONICAL_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const AUDIT_LIMIT = /^(?:[1-9]|[1-9]\d|100)$/;
const AUDIT_CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const TENANT_AUDIT_LIST_PATH = "/api/v1/audit-logs/";
const TENANT_AUDIT_FILTERS = new Set([
  "action",
  "module",
  "outcome",
  "target_type",
  "target_id",
  "limit",
  "after",
]);
const MEMBER_PAYMENT_HISTORY_FILTERS = new Set(["limit", "after"]);
const CHECK_IN_LIST_FILTERS = new Set(["member_id", "open", "limit", "after"]);
const MEMBER_LIST_PATH = "/api/v1/members/";
const MEMBERSHIP_PLAN_LIST_PATH = "/api/v1/membership-plans/";
const MEMBERSHIP_LIST_PATH = "/api/v1/memberships/";
const MEMBERSHIP_EXPIRY_PATH = "/api/v1/memberships/expiry/";
const MEMBER_LIST_FILTERS = new Set([
  "status",
  "search",
  "joined_from",
  "joined_to",
  "is_pt_client",
  "limit",
  "after",
]);
const MEMBER_LIST_STATUSES = new Set([
  "ACTIVE",
  "EXPIRED",
  "SUSPENDED",
  "CANCELLED",
  "ARCHIVED",
]);
const MEMBERSHIP_PLAN_LIST_FILTERS = new Set(["limit", "after"]);
const MEMBERSHIP_LIST_FILTERS = new Set([
  "status",
  "member_id",
  "payment_status",
  "limit",
  "after",
]);
const MEMBERSHIP_LIST_STATUSES = new Set([
  "PENDING",
  "ACTIVE",
  "EXPIRED",
  "SUSPENDED",
  "CANCELLED",
]);
const MEMBERSHIP_PAYMENT_STATUSES = new Set([
  "UNPAID",
  "PARTIAL",
  "PAID",
  "PARTIALLY_REFUNDED",
  "REFUNDED",
]);
const MEMBERSHIP_EXPIRY_FILTERS = new Set(["bucket", "limit", "after"]);
const MEMBERSHIP_EXPIRY_BUCKETS = new Set([
  "expired",
  "today",
  "within_3_days",
  "within_7_days",
  "within_30_days",
]);
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

const ALLOWED_PLATFORM_PATHS = [
  /^\/api\/v1\/admin\/gyms\/$/,
  /^\/api\/v1\/admin\/gyms\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(?:activate\/|suspend\/)?$/,
  /^\/api\/v1\/admin\/gyms\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/owners\/$/,
  /^\/api\/v1\/admin\/subscriptions\/$/,
  PLATFORM_DASHBOARD_PATH,
  PLATFORM_SUBSCRIPTION_DETAIL_PATH,
  /^\/api\/v1\/admin\/audit-logs\/$/,
];

const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{16,128}$/;

const SECRET_KEYS = new Set([
  "access_token",
  "refresh_token",
  "token",
  "refresh_session",
  "gym_selection_ticket",
  "mfa_challenge_token",
  "invitation_token",
  "reset_token",
]);

const INBOUND_SECRET_KEYS = new Set([
  "access_token",
  "refresh_token",
  "token",
  "refresh_session",
  "gym_selection_ticket",
  "mfa_challenge_token",
]);

export const BFF_SECRET_HEADER = "X-Gym-BFF-Secret";

export function stripInboundSecrets(body: JsonRecord): JsonRecord {
  return Object.fromEntries(
    Object.entries(body).filter(([key]) => !INBOUND_SECRET_KEYS.has(key)),
  );
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function stripCredentials(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripCredentials);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !SECRET_KEYS.has(key))
      .map(([key, child]) => [key, stripCredentials(child)]),
  );
}

function findString(value: unknown, key: string): string | undefined {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = findString(child, key);
      if (found) return found;
    }
  } else if (isRecord(value)) {
    if (typeof value[key] === "string") return value[key];
    for (const child of Object.values(value)) {
      const found = findString(child, key);
      if (found) return found;
    }
  }
  return undefined;
}

function maxAge(expiresAt: string | undefined): number | undefined {
  if (!expiresAt) return undefined;
  const milliseconds = Date.parse(expiresAt) - Date.now();
  return Number.isFinite(milliseconds)
    ? Math.max(0, Math.floor(milliseconds / 1000))
    : undefined;
}

async function parseJson(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function canonicalDjangoPath(path: string): string {
  const queryIndex = path.indexOf("?");
  const rawPathname = queryIndex === -1 ? path : path.slice(0, queryIndex);
  if (
    !rawPathname.startsWith("/api/v1/") ||
    rawPathname.includes("%") ||
    rawPathname.includes("\\") ||
    rawPathname.includes("#")
  ) {
    throw new Error("Django route is not allowed.");
  }

  const parsed = new URL(path, "http://internal");
  if (
    parsed.origin !== "http://internal" ||
    parsed.pathname !== rawPathname ||
    parsed.hash
  ) {
    throw new Error("Django route is not allowed.");
  }
  return `${parsed.pathname}${parsed.search}`;
}

function strictQueryValues(
  search: string,
  allowed: ReadonlySet<string>,
): Map<string, string> | null {
  if (search.length === 0) return new Map();
  const values = new Map<string, string>();
  for (const [key, value] of new URLSearchParams(search).entries()) {
    if (!allowed.has(key) || values.has(key)) return null;
    values.set(key, value);
  }
  return values;
}

function allowsSubscriptionListQuery(search: string): boolean {
  const values = strictQueryValues(search, new Set(["status"]));
  if (!values) return false;
  const status = values.get("status");
  if (status === undefined) return values.size === 0;
  return (
    status.length > 0 &&
    status.length <= 16 &&
    SUBSCRIPTION_LIST_STATUS.has(status)
  );
}

function allowsAuditListQuery(search: string): boolean {
  const values = strictQueryValues(search, AUDIT_FILTERS);
  if (!values) return false;
  const targetGymId = values.get("target_gym_id");
  if (targetGymId !== undefined && !CANONICAL_UUID.test(targetGymId)) {
    return false;
  }
  const action = values.get("action");
  if (
    action !== undefined &&
    (action.length === 0 || action.length > 100 || action.trim().length === 0)
  ) {
    return false;
  }
  const outcome = values.get("outcome");
  if (outcome !== undefined && !AUDIT_OUTCOMES.has(outcome)) return false;
  const limit = values.get("limit");
  if (limit !== undefined && !AUDIT_LIMIT.test(limit)) return false;
  const after = values.get("after");
  if (after !== undefined && !AUDIT_CURSOR.test(after)) return false;
  return true;
}

function allowsExactPlatformQuery(pathname: string, search: string): boolean {
  if (
    PLATFORM_DASHBOARD_PATH.test(pathname) ||
    PLATFORM_SUBSCRIPTION_DETAIL_PATH.test(pathname)
  ) {
    return search.length === 0;
  }
  if (pathname === PLATFORM_SUBSCRIPTION_LIST_PATH) {
    return allowsSubscriptionListQuery(search);
  }
  if (pathname === PLATFORM_AUDIT_LIST_PATH) {
    return allowsAuditListQuery(search);
  }
  return true;
}

function allowsBoundedText(
  value: string | undefined,
  maxLength: number,
): boolean {
  if (value === undefined) return true;
  return (
    value.length > 0 && value.length <= maxLength && value.trim().length > 0
  );
}

function allowsCheckInListQuery(search: string): boolean {
  const values = strictQueryValues(search, CHECK_IN_LIST_FILTERS);
  if (!values || !allowsCleanValues(values)) return false;
  const memberId = values.get("member_id");
  if (memberId !== undefined && !CANONICAL_UUID.test(memberId)) return false;
  const open = values.get("open");
  if (open !== undefined && open !== "true" && open !== "false") return false;
  return (
    allowsLimitValue(values.get("limit")) &&
    allowsCursorValue(values.get("after"))
  );
}

function allowsMemberPaymentHistoryQuery(search: string): boolean {
  const values = strictQueryValues(search, MEMBER_PAYMENT_HISTORY_FILTERS);
  if (!values) return false;
  const limit = values.get("limit");
  if (limit !== undefined && !AUDIT_LIMIT.test(limit)) return false;
  const after = values.get("after");
  if (after !== undefined && !AUDIT_CURSOR.test(after)) return false;
  return true;
}

function allowsLimitValue(value: string | undefined): boolean {
  return value === undefined || AUDIT_LIMIT.test(value);
}

function allowsCursorValue(value: string | undefined): boolean {
  return value === undefined || AUDIT_CURSOR.test(value);
}

function hasQueryMeta(value: string): boolean {
  return /[\u0000-\u001f\u007f&#?]/.test(value);
}

function allowsIsoDateValue(value: string | undefined): boolean {
  if (value === undefined) return true;
  const match = ISO_DATE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

function allowsCleanValues(values: ReadonlyMap<string, string>): boolean {
  for (const value of values.values()) {
    if (hasQueryMeta(value)) return false;
  }
  return true;
}

function allowsGetOnlyQuery(
  method: string | undefined,
  search: string,
  allows: (search: string) => boolean,
): boolean {
  if (method !== "GET") return search.length === 0;
  return allows(search);
}

function allowsMemberListQuery(search: string): boolean {
  const values = strictQueryValues(search, MEMBER_LIST_FILTERS);
  if (!values || !allowsCleanValues(values)) return false;
  const status = values.get("status");
  if (status !== undefined && !MEMBER_LIST_STATUSES.has(status)) return false;
  const text = values.get("search");
  if (text !== undefined && (text.length > 100 || text.trim().length === 0)) {
    return false;
  }
  const joinedFrom = values.get("joined_from");
  const joinedTo = values.get("joined_to");
  if (!allowsIsoDateValue(joinedFrom) || !allowsIsoDateValue(joinedTo)) {
    return false;
  }
  if (
    joinedFrom !== undefined &&
    joinedTo !== undefined &&
    joinedFrom > joinedTo
  ) {
    return false;
  }
  const ptClient = values.get("is_pt_client");
  if (ptClient !== undefined && ptClient !== "true" && ptClient !== "false") {
    return false;
  }
  return (
    allowsLimitValue(values.get("limit")) &&
    allowsCursorValue(values.get("after"))
  );
}

function allowsMembershipPlanListQuery(search: string): boolean {
  const values = strictQueryValues(search, MEMBERSHIP_PLAN_LIST_FILTERS);
  if (!values || !allowsCleanValues(values)) return false;
  return (
    allowsLimitValue(values.get("limit")) &&
    allowsCursorValue(values.get("after"))
  );
}

function allowsMembershipListQuery(search: string): boolean {
  const values = strictQueryValues(search, MEMBERSHIP_LIST_FILTERS);
  if (!values || !allowsCleanValues(values)) return false;
  const status = values.get("status");
  if (status !== undefined && !MEMBERSHIP_LIST_STATUSES.has(status)) {
    return false;
  }
  const memberId = values.get("member_id");
  if (memberId !== undefined && !CANONICAL_UUID.test(memberId)) return false;
  const paymentStatus = values.get("payment_status");
  if (
    paymentStatus !== undefined &&
    !MEMBERSHIP_PAYMENT_STATUSES.has(paymentStatus)
  ) {
    return false;
  }
  return (
    allowsLimitValue(values.get("limit")) &&
    allowsCursorValue(values.get("after"))
  );
}

function allowsMembershipExpiryQuery(search: string): boolean {
  const values = strictQueryValues(search, MEMBERSHIP_EXPIRY_FILTERS);
  if (!values || !allowsCleanValues(values)) return false;
  const bucket = values.get("bucket");
  if (bucket === undefined || !MEMBERSHIP_EXPIRY_BUCKETS.has(bucket)) {
    return false;
  }
  return (
    allowsLimitValue(values.get("limit")) &&
    allowsCursorValue(values.get("after"))
  );
}

function allowsTenantAuditListQuery(search: string): boolean {
  const values = strictQueryValues(search, TENANT_AUDIT_FILTERS);
  if (!values) return false;
  if (!allowsBoundedText(values.get("action"), 100)) return false;
  if (!allowsBoundedText(values.get("module"), 50)) return false;
  const outcome = values.get("outcome");
  if (outcome !== undefined && !AUDIT_OUTCOMES.has(outcome)) return false;
  if (!allowsBoundedText(values.get("target_type"), 100)) return false;
  const targetId = values.get("target_id");
  if (targetId !== undefined && !CANONICAL_UUID.test(targetId)) return false;
  const limit = values.get("limit");
  if (limit !== undefined && !AUDIT_LIMIT.test(limit)) return false;
  const after = values.get("after");
  if (after !== undefined && !AUDIT_CURSOR.test(after)) return false;
  return true;
}

function allowsForwardedQuery(
  pathname: string,
  search: string,
  method: string | undefined,
): boolean {
  const phase5 = allowsPhase5Route(pathname, search, method);
  if (phase5 !== null) return phase5;
  const reports = allowsReportRoute(pathname, search, method);
  if (reports !== null) return reports;
  const finance = allowsFinanceRoute(pathname, search, method);
  if (finance !== null) return finance;
  if (pathname === TENANT_AUDIT_LIST_PATH) {
    return method === "GET" && allowsTenantAuditListQuery(search);
  }
  if (MEMBER_PAYMENT_HISTORY_PATH.test(pathname)) {
    return method === "GET" && allowsMemberPaymentHistoryQuery(search);
  }
  if (MEMBER_CHECK_IN_HISTORY_PATH.test(pathname)) {
    return method === "GET" && allowsMemberPaymentHistoryQuery(search);
  }
  if (pathname === CHECK_IN_LIST_PATH) {
    if (method === "GET") return allowsCheckInListQuery(search);
    return method === "POST" && search.length === 0;
  }
  if (CHECK_IN_DETAIL_PATH.test(pathname)) {
    return method === "GET" && search.length === 0;
  }
  if (CHECK_IN_CHECKOUT_PATH.test(pathname)) {
    return method === "POST" && search.length === 0;
  }
  if (pathname === CHECK_IN_CHALLENGE_PATH) {
    return method === "POST" && search.length === 0;
  }
  if (pathname === MEMBER_LIST_PATH) {
    return allowsGetOnlyQuery(method, search, allowsMemberListQuery);
  }
  if (pathname === MEMBERSHIP_PLAN_LIST_PATH) {
    return allowsGetOnlyQuery(method, search, allowsMembershipPlanListQuery);
  }
  if (pathname === MEMBERSHIP_LIST_PATH) {
    return allowsGetOnlyQuery(method, search, allowsMembershipListQuery);
  }
  if (pathname === MEMBERSHIP_EXPIRY_PATH) {
    return allowsGetOnlyQuery(method, search, allowsMembershipExpiryQuery);
  }
  if (
    pathname === FILE_UPLOAD_INTENT_PATH ||
    FILE_COMPLETE_PATH.test(pathname)
  ) {
    return method === "POST" && search.length === 0;
  }
  if (FILE_DOWNLOAD_PATH.test(pathname)) {
    return method === "GET" && search.length === 0;
  }
  if (FILE_DELETE_PATH.test(pathname)) {
    return method === "DELETE" && search.length === 0;
  }
  return allowsExactPlatformQuery(pathname, search);
}

type BffContext = {
  requestId: string;
  method: string;
  route: string;
};

const bffContext = new AsyncLocalStorage<BffContext>();

function runWithBffRequest<T>(
  request: NextRequest,
  route: string,
  fn: () => Promise<T>,
): Promise<T> {
  return bffContext.run(
    {
      requestId: resolveRequestId(request.headers.get("x-request-id")),
      method: request.method,
      route: normalizedRoute(route),
    },
    fn,
  );
}

function stamp(response: NextResponse): NextResponse {
  const context = bffContext.getStore();
  if (context) response.headers.set("X-Request-ID", context.requestId);
  return response;
}

function failureOutcome(status: number, body: unknown): string | null {
  const code = errorCode(body);
  if (
    code === "UPSTREAM_UNAVAILABLE" ||
    code === "INVALID_UPSTREAM_RESPONSE" ||
    code === "CSRF_FAILED" ||
    code === "AUTHENTICATION_REQUIRED" ||
    code === "AUTHENTICATION_FAILED" ||
    code === "BFF_REQUIRED" ||
    code === "ACCESS_TOKEN_EXPIRED"
  ) {
    return code;
  }
  if (status === 401 || status === 403) return "authentication_failed";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "upstream_error";
  return null;
}

function finishBffResponse(
  response: NextResponse,
  body: unknown,
): NextResponse {
  const context = bffContext.getStore();
  if (!context) return response;
  response.headers.set("X-Request-ID", context.requestId);
  const outcome = failureOutcome(response.status, body);
  if (outcome) {
    logBffFailure({
      requestId: context.requestId,
      route: context.route,
      method: context.method,
      status: response.status,
      outcome,
    });
  }
  return response;
}

async function callDjango(path: string, init: RequestInit): Promise<Response> {
  const canonicalPath = canonicalDjangoPath(path);
  const parsed = new URL(canonicalPath, "http://internal");
  const pathname = parsed.pathname;
  const allowed =
    ALLOWED_PATHS.has(pathname) ||
    ALLOWED_TENANT_PATHS.some((pattern) => pattern.test(pathname)) ||
    ALLOWED_PLATFORM_PATHS.some((pattern) => pattern.test(pathname));
  if (!allowed || !allowsForwardedQuery(pathname, parsed.search, init.method)) {
    throw new Error("Django route is not allowed.");
  }
  const { djangoApiUrl, bffSharedSecret, secureCookies } = getServerEnv();
  const context = bffContext.getStore();
  return fetch(`${djangoApiUrl}${canonicalPath}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      [BFF_SECRET_HEADER]: bffSharedSecret,
      ...init.headers,
      ...(context ? { "X-Request-ID": context.requestId } : {}),
      // Production Django redirects plain HTTP. This marks the private hop
      // after the browser already used HTTPS at Caddy. It is not a TLS proof.
      ...(secureCookies ? { "X-Forwarded-Proto": "https" } : {}),
    },
  });
}

type UpstreamPresenter = (status: number, body: unknown) => unknown;

function responseFromUpstream(
  status: number,
  body: unknown,
  present?: UpstreamPresenter,
): NextResponse {
  const headers = { "Cache-Control": "no-store, private" };
  if (status === 204) {
    return finishBffResponse(new NextResponse(null, { status, headers }), body);
  }
  if (body === null) {
    return finishBffResponse(
      NextResponse.json(
        {
          success: false,
          error: {
            code: "INVALID_UPSTREAM_RESPONSE",
            message: "The service returned an invalid response.",
          },
        },
        { status: 502, headers },
      ),
      body,
    );
  }
  return finishBffResponse(
    NextResponse.json(
      present ? present(status, body) : stripCredentials(body),
      {
        status,
        headers,
      },
    ),
    body,
  );
}

function setTokenCookies(response: NextResponse, body: unknown): void {
  const { secureCookies } = getServerEnv();
  const access = findString(body, "access_token");
  const refresh = findString(body, "refresh_token");
  const ticket = findString(body, "gym_selection_ticket");
  const mfaChallenge = findString(body, "mfa_challenge_token");
  if (access) {
    response.cookies.set(
      AUTH_COOKIES.access,
      access,
      authCookieOptions(
        secureCookies,
        maxAge(findString(body, "access_expires_at")),
      ),
    );
    clearSelectionCookies(response);
  }
  if (refresh) {
    response.cookies.set(
      AUTH_COOKIES.refresh,
      refresh,
      authCookieOptions(
        secureCookies,
        maxAge(findString(body, "refresh_expires_at")),
      ),
    );
  }
  if (ticket) {
    const ticketMaxAge =
      maxAge(findString(body, "gym_selection_expires_at")) ??
      SELECTION_COOKIE_MAX_AGE;
    const selectionAge = Math.min(ticketMaxAge, SELECTION_COOKIE_MAX_AGE);
    response.cookies.set(
      AUTH_COOKIES.selectionTicket,
      ticket,
      authCookieOptions(secureCookies, selectionAge),
    );
    const data = isRecord(body) && isRecord(body.data) ? body.data : null;
    const gyms =
      data && Array.isArray(data.available_gym_memberships)
        ? data.available_gym_memberships
        : [];
    response.cookies.set(
      AUTH_COOKIES.selectionChoices,
      JSON.stringify({
        gyms,
        expires_at: data?.gym_selection_expires_at ?? null,
      }),
      authCookieOptions(secureCookies, selectionAge),
    );
    if (!access) {
      response.cookies.set(
        AUTH_COOKIES.access,
        "",
        clearCookieOptions(secureCookies, true),
      );
      response.cookies.set(
        AUTH_COOKIES.refresh,
        "",
        clearCookieOptions(secureCookies, true),
      );
    }
  }
  if (mfaChallenge) {
    response.cookies.set(
      AUTH_COOKIES.mfaChallenge,
      mfaChallenge,
      authCookieOptions(
        secureCookies,
        Math.min(
          maxAge(findString(body, "mfa_challenge_expires_at")) ?? 300,
          300,
        ),
      ),
    );
  }
  if (access) {
    response.cookies.set(
      AUTH_COOKIES.mfaChallenge,
      "",
      clearCookieOptions(secureCookies, true),
    );
  }
}

function clearSelectionCookies(response: NextResponse): void {
  const { secureCookies } = getServerEnv();
  response.cookies.set(
    AUTH_COOKIES.selectionTicket,
    "",
    clearCookieOptions(secureCookies, true),
  );
  response.cookies.set(
    AUTH_COOKIES.selectionChoices,
    "",
    clearCookieOptions(secureCookies, true),
  );
}

export function clearAuthCookies(response: NextResponse): void {
  const { secureCookies } = getServerEnv();
  response.cookies.set(
    AUTH_COOKIES.access,
    "",
    clearCookieOptions(secureCookies, true),
  );
  response.cookies.set(
    AUTH_COOKIES.refresh,
    "",
    clearCookieOptions(secureCookies, true),
  );
  clearSelectionCookies(response);
  response.cookies.set(
    AUTH_COOKIES.mfaChallenge,
    "",
    clearCookieOptions(secureCookies, true),
  );
}

export async function parseRequestBody(
  request: NextRequest,
): Promise<JsonRecord | null> {
  const text = await request.text();
  if (text.length > 16_384) return null;
  try {
    const parsed: unknown = text ? JSON.parse(text) : {};
    return isRecord(parsed) ? stripInboundSecrets(parsed) : null;
  } catch {
    return null;
  }
}

export function authMutation(
  request: NextRequest,
  path: string,
  body: JsonRecord,
  options: {
    includeAccess?: boolean;
    includeRefresh?: boolean;
    includeSelectionTicket?: boolean;
    includeMfaChallenge?: boolean;
    clearAfter?: boolean;
  } = {},
): Promise<NextResponse> {
  return runWithBffRequest(request, path, () =>
    authMutationBound(request, path, body, options),
  );
}

async function authMutationBound(
  request: NextRequest,
  path: string,
  body: JsonRecord,
  options: {
    includeAccess?: boolean;
    includeRefresh?: boolean;
    includeSelectionTicket?: boolean;
    includeMfaChallenge?: boolean;
    clearAfter?: boolean;
  } = {},
): Promise<NextResponse> {
  const env = getServerEnv();
  const verified = verifyMutationRequest(request, env.allowedOrigins);
  if (!verified.ok) {
    return responseFromUpstream(403, {
      success: false,
      error: { code: "CSRF_FAILED", message: verified.message },
    });
  }

  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  const refresh = request.cookies.get(AUTH_COOKIES.refresh)?.value;
  const selectionTicket = request.cookies.get(
    AUTH_COOKIES.selectionTicket,
  )?.value;
  const mfaChallenge = request.cookies.get(AUTH_COOKIES.mfaChallenge)?.value;
  const upstreamBody = {
    ...stripInboundSecrets(body),
    ...(options.includeRefresh && refresh ? { refresh_token: refresh } : {}),
    ...(options.includeSelectionTicket && selectionTicket
      ? { gym_selection_ticket: selectionTicket }
      : {}),
    ...(options.includeMfaChallenge && mfaChallenge
      ? { challenge_token: mfaChallenge }
      : {}),
  };

  try {
    const upstream = await callDjango(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(options.includeAccess && access
          ? { Authorization: `Bearer ${access}` }
          : {}),
      },
      body: JSON.stringify(upstreamBody),
    });
    const upstreamJson = await parseJson(upstream);
    const response = responseFromUpstream(upstream.status, upstreamJson);
    if (upstream.ok) setTokenCookies(response, upstreamJson);
    if (options.clearAfter) clearAuthCookies(response);
    if (!upstream.ok && options.includeSelectionTicket) {
      const code = errorCode(upstreamJson);
      if (code && code !== "CSRF_FAILED") {
        const terminal = ["AUTHENTICATION_FAILED", "ACCESS_TOKEN_EXPIRED"];
        if (terminal.includes(code) || upstream.status === 401) {
          clearSelectionCookies(response);
        }
      }
    }
    return response;
  } catch {
    const response = responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
    if (options.clearAfter) clearAuthCookies(response);
    return response;
  }
}

function errorCode(body: unknown): string | undefined {
  if (!isRecord(body) || !isRecord(body.error)) return undefined;
  return typeof body.error.code === "string" ? body.error.code : undefined;
}

async function refreshAndRetry(
  request: NextRequest,
  expiredBody: unknown,
  retry: (accessToken: string) => Promise<Response>,
  present?: UpstreamPresenter,
): Promise<NextResponse> {
  const env = getServerEnv();
  const verified = verifyMutationRequest(request, env.allowedOrigins);
  const refresh = request.cookies.get(AUTH_COOKIES.refresh)?.value;
  if (!verified.ok || !refresh) {
    return responseFromUpstream(401, expiredBody, present);
  }

  const refreshed = await callDjango("/api/v1/auth/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  const refreshedBody = await parseJson(refreshed);
  if (!refreshed.ok) {
    const failed = responseFromUpstream(
      refreshed.status,
      refreshedBody,
      present,
    );
    clearAuthCookies(failed);
    return failed;
  }

  const newAccess = findString(refreshedBody, "access_token");
  if (!newAccess) {
    const invalid = responseFromUpstream(502, null, present);
    clearAuthCookies(invalid);
    return invalid;
  }

  try {
    const upstream = await retry(newAccess);
    const response = responseFromUpstream(
      upstream.status,
      await parseJson(upstream),
      present,
    );
    if (upstream.status === 401) {
      clearAuthCookies(response);
    } else {
      setTokenCookies(response, refreshedBody);
    }
    return response;
  } catch {
    const unavailable = responseFromUpstream(
      503,
      {
        success: false,
        error: {
          code: "UPSTREAM_UNAVAILABLE",
          message: "The service is temporarily unavailable. Please try again.",
        },
      } satisfies ApiErrorBody,
      present,
    );
    setTokenCookies(unavailable, refreshedBody);
    return unavailable;
  }
}

export function authenticatedGet(
  request: NextRequest,
  path: string,
  present?: UpstreamPresenter,
): Promise<NextResponse> {
  return runWithBffRequest(request, path, () =>
    authenticatedGetBound(request, path, present),
  );
}

async function authenticatedGetBound(
  request: NextRequest,
  path: string,
  present?: UpstreamPresenter,
): Promise<NextResponse> {
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }

  try {
    const upstream = await callDjango(path, {
      method: "GET",
      headers: { Authorization: `Bearer ${access}` },
    });
    const body = await parseJson(upstream);
    const env = getServerEnv();
    if (upstream.status === 401 && errorCode(body) === env.accessExpiredCode) {
      return refreshAndRetry(
        request,
        body,
        (newAccess) =>
          callDjango(path, {
            method: "GET",
            headers: { Authorization: `Bearer ${newAccess}` },
          }),
        present,
      );
    }
    return responseFromUpstream(upstream.status, body, present);
  } catch {
    return responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    } satisfies ApiErrorBody);
  }
}

const RECEIPT_DOCUMENT_CSP =
  "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'";

function receiptFileName(header: string | null): string {
  const match = /^attachment;\s*filename="([A-Za-z0-9_-]{1,80}\.html)"$/.exec(
    header ?? "",
  );
  return match?.[1] ?? "receipt.html";
}

const CSV_FILENAME =
  /^(?:report|(?:daily|monthly|cash-book|profit-loss|product-sales|membership-expiry|check-ins|pt-sessions|classes|stock-movements|inventory)-[A-Z0-9][A-Z0-9-]{1,31}(?:-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2})?)\.csv$/;

function csvFileName(header: string | null): string {
  const match = /^attachment;\s*filename="([^"]+)"$/.exec(header ?? "");
  const name = match?.[1] ?? "";
  return CSV_FILENAME.test(name) ? name : "report.csv";
}

function csvDocumentResponse(
  status: number,
  body: string,
  disposition: string | null,
): NextResponse {
  return stamp(
    new NextResponse(body, {
      status,
      headers: {
        "Cache-Control": "no-store, private",
        "Content-Disposition": `attachment; filename="${csvFileName(disposition)}"`,
        "Content-Type": "text/csv; charset=utf-8",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  );
}

function receiptHtmlResponse(
  status: number,
  html: string,
  disposition: string | null,
): NextResponse {
  if (/<script|javascript:/i.test(html)) {
    return responseFromUpstream(502, {
      success: false,
      error: {
        code: "INVALID_UPSTREAM_RESPONSE",
        message: "The receipt could not be downloaded.",
      },
    });
  }
  return stamp(
    new NextResponse(html, {
      status,
      headers: {
        "Cache-Control": "no-store, private",
        "Content-Disposition": `attachment; filename="${receiptFileName(disposition)}"`,
        "Content-Security-Policy": RECEIPT_DOCUMENT_CSP,
        "Content-Type": "text/html; charset=utf-8",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  );
}

async function responseForDocument(upstream: Response): Promise<NextResponse> {
  const type = upstream.headers.get("content-type") ?? "";
  if (upstream.ok && type.includes("text/csv")) {
    return csvDocumentResponse(
      upstream.status,
      await upstream.text(),
      upstream.headers.get("content-disposition"),
    );
  }
  if (upstream.ok && type.includes("text/html")) {
    return receiptHtmlResponse(
      upstream.status,
      await upstream.text(),
      upstream.headers.get("content-disposition"),
    );
  }
  return responseFromUpstream(upstream.status, await parseJson(upstream));
}

export function authenticatedDocumentGet(
  request: NextRequest,
  path: string,
  documentKind: "html" | "csv" = "html",
): Promise<NextResponse> {
  return runWithBffRequest(request, path, () =>
    authenticatedDocumentGetBound(request, path, documentKind),
  );
}

async function authenticatedDocumentGetBound(
  request: NextRequest,
  path: string,
  documentKind: "html" | "csv" = "html",
): Promise<NextResponse> {
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }
  const accept =
    documentKind === "csv"
      ? "text/csv, application/json"
      : "text/html, application/json";
  const perform = (accessToken: string) =>
    callDjango(path, {
      method: "GET",
      headers: {
        Accept: accept,
        Authorization: `Bearer ${accessToken}`,
      },
    });
  try {
    const upstream = await perform(access);
    const type = upstream.headers.get("content-type") ?? "";
    if (upstream.status === 401 && type.includes("application/json")) {
      const body = await parseJson(upstream);
      const env = getServerEnv();
      if (errorCode(body) === env.accessExpiredCode) {
        const envCheck = getServerEnv();
        const verified = verifyMutationRequest(
          request,
          envCheck.allowedOrigins,
        );
        const refresh = request.cookies.get(AUTH_COOKIES.refresh)?.value;
        if (!verified.ok || !refresh) return responseFromUpstream(401, body);
        const refreshed = await callDjango("/api/v1/auth/refresh/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        const refreshedBody = await parseJson(refreshed);
        if (!refreshed.ok) {
          const failed = responseFromUpstream(refreshed.status, refreshedBody);
          clearAuthCookies(failed);
          return failed;
        }
        const newAccess = findString(refreshedBody, "access_token");
        if (!newAccess) {
          const invalid = responseFromUpstream(502, null);
          clearAuthCookies(invalid);
          return invalid;
        }
        const retried = await perform(newAccess);
        const response = await responseForDocument(retried);
        if (retried.status === 401) clearAuthCookies(response);
        else setTokenCookies(response, refreshedBody);
        return response;
      }
      return responseFromUpstream(401, body);
    }
    return responseForDocument(upstream);
  } catch {
    return responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    } satisfies ApiErrorBody);
  }
}

function idempotencyKeyForMutation(
  path: string,
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  suppliedKey: string | null,
): string | null | "rejected" {
  if (method === "DELETE") return suppliedKey;
  const queryIndex = path.indexOf("?");
  const pathname = queryIndex === -1 ? path : path.slice(0, queryIndex);
  const search = queryIndex === -1 ? "" : path.slice(queryIndex);
  const phase5 = phase5Idempotency(pathname, method);
  if (phase5 === "rejected") return suppliedKey ? "rejected" : null;
  if (suppliedKey) return suppliedKey;
  if (phase5 === "required") return crypto.randomUUID();
  if (phase5 === "none") return null;
  // Finance commands require a key. Other published commands, including
  // check-in challenges, reject a key the caller did not send.
  if (allowsFinanceRoute(pathname, search, method) !== true) return null;
  return crypto.randomUUID();
}

export function authenticatedMutation(
  request: NextRequest,
  path: string,
  method: "POST" | "PATCH" | "PUT" | "DELETE" = "POST",
): Promise<NextResponse> {
  return runWithBffRequest(request, path, () =>
    authenticatedMutationBound(request, path, method),
  );
}

async function authenticatedMutationBound(
  request: NextRequest,
  path: string,
  method: "POST" | "PATCH" | "PUT" | "DELETE" = "POST",
): Promise<NextResponse> {
  if (method === "DELETE") {
    const text = await request.text();
    if (text.trim().length > 0) {
      return responseFromUpstream(400, {
        success: false,
        error: { code: "INVALID_REQUEST", message: "Invalid request body." },
      });
    }
  }
  const body = method === "DELETE" ? {} : await parseRequestBody(request);
  if (body === null) {
    return responseFromUpstream(400, {
      success: false,
      error: { code: "INVALID_REQUEST", message: "Invalid request body." },
    });
  }
  const env = getServerEnv();
  const verified = verifyMutationRequest(request, env.allowedOrigins);
  if (!verified.ok) {
    return responseFromUpstream(403, {
      success: false,
      error: { code: "CSRF_FAILED", message: verified.message },
    });
  }
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }
  const suppliedKey = request.headers.get("Idempotency-Key");
  const idempotencyKey = idempotencyKeyForMutation(path, method, suppliedKey);
  if (idempotencyKey === "rejected") {
    return responseFromUpstream(400, {
      success: false,
      error: {
        code: "INVALID_REQUEST",
        message: "Idempotency-Key is not accepted for a trainer check-in scan.",
      },
    });
  }
  if (idempotencyKey && !IDEMPOTENCY_KEY_PATTERN.test(idempotencyKey)) {
    return responseFromUpstream(400, {
      success: false,
      error: {
        code: "INVALID_IDEMPOTENCY_KEY",
        message: "The idempotency key is invalid.",
      },
    });
  }
  try {
    const perform = (accessToken: string) =>
      callDjango(path, {
        method,
        headers: {
          Authorization: `Bearer ${accessToken}`,
          ...(method === "DELETE"
            ? {}
            : { "Content-Type": "application/json" }),
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        },
        ...(method === "DELETE" ? {} : { body: JSON.stringify(body) }),
      });
    const upstream = await perform(access);
    const upstreamBody = await parseJson(upstream);
    if (
      upstream.status === 401 &&
      errorCode(upstreamBody) === env.accessExpiredCode
    ) {
      return refreshAndRetry(request, upstreamBody, perform);
    }
    return responseFromUpstream(upstream.status, upstreamBody);
  } catch {
    return responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
  }
}

export function getTenantGymProfile(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedGet(request, "/api/v1/gym/");
}

export function updateTenantGymProfile(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedMutation(request, "/api/v1/gym/", "PATCH");
}

const LOGO_BYTES = 1_200_000;
const LOGO_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export function getTenantGymLogo(request: NextRequest): Promise<NextResponse> {
  return runWithBffRequest(request, "/api/v1/gym/logo/", () =>
    getTenantGymLogoBound(request),
  );
}

async function getTenantGymLogoBound(
  request: NextRequest,
): Promise<NextResponse> {
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }
  try {
    const upstream = await callDjango("/api/v1/gym/logo/", {
      method: "GET",
      headers: {
        Accept: "image/jpeg, image/png, image/webp, application/json",
        Authorization: `Bearer ${access}`,
      },
    });
    const type = (
      (upstream.headers.get("content-type") ?? "").split(";")[0] ?? ""
    )
      .trim()
      .toLowerCase();
    if (upstream.ok && LOGO_TYPES.has(type)) {
      return finishBffResponse(
        new NextResponse(await upstream.arrayBuffer(), {
          status: 200,
          headers: {
            "Cache-Control": "no-store, private",
            "Content-Type": type,
            "X-Content-Type-Options": "nosniff",
          },
        }),
        null,
      );
    }
    return responseFromUpstream(upstream.status, await parseJson(upstream));
  } catch {
    return responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
  }
}

export function updateTenantGymLogo(
  request: NextRequest,
): Promise<NextResponse> {
  return runWithBffRequest(request, "/api/v1/gym/logo/", () =>
    updateTenantGymLogoBound(request),
  );
}

async function updateTenantGymLogoBound(
  request: NextRequest,
): Promise<NextResponse> {
  const env = getServerEnv();
  const verified = verifyMutationRequest(request, env.allowedOrigins);
  if (!verified.ok) {
    return responseFromUpstream(403, {
      success: false,
      error: { code: "CSRF_FAILED", message: verified.message },
    });
  }
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
    return responseFromUpstream(400, {
      success: false,
      error: { code: "INVALID_REQUEST", message: "Choose a logo image." },
    });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength < 1 || body.byteLength > LOGO_BYTES) {
    return responseFromUpstream(400, {
      success: false,
      error: {
        code: "INVALID_REQUEST",
        message: "The logo must be a JPEG, PNG, or WebP under 1 MB.",
      },
    });
  }
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }
  try {
    const upstream = await callDjango("/api/v1/gym/logo/", {
      method: "PUT",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${access}`,
        "Content-Type": contentType,
      },
      body,
    });
    return responseFromUpstream(upstream.status, await parseJson(upstream));
  } catch {
    return responseFromUpstream(503, {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
  }
}

export function deleteTenantGymLogo(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedMutation(request, "/api/v1/gym/logo/", "DELETE");
}

export function getTenantGymSettings(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedGet(request, "/api/v1/settings/");
}

export function updateTenantGymSettings(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedMutation(request, "/api/v1/settings/", "PATCH");
}

const STAFF_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STAFF_LIST_FILTERS = ["status", "membership_status", "search"] as const;
const STAFF_LIST_LIMIT = /^(?:[1-9]|[1-9]\d|100)$/;
const STAFF_LIST_CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;

export function normalizeResourceId(value: string): string | null {
  const resourceId = value.toLowerCase();
  return STAFF_ID_PATTERN.test(resourceId) ? resourceId : null;
}

export function normalizeStaffId(value: string): string | null {
  return normalizeResourceId(value);
}

export function unknownStaffResponse(): NextResponse {
  return NextResponse.json(
    {
      success: false,
      error: { code: "NOT_FOUND", message: "Staff was not found." },
    },
    { status: 404, headers: { "Cache-Control": "no-store, private" } },
  );
}

function invalidStaffListQuery(field: "limit" | "after"): NextResponse {
  const message =
    field === "limit" ? "Limit must be from 1 to 100." : "Invalid cursor.";
  return responseFromUpstream(400, {
    success: false,
    error: {
      code: "INVALID_REQUEST",
      message,
      fields: { [field]: [message] },
    },
  });
}

export function listTenantStaff(
  request: NextRequest,
): Promise<NextResponse> | NextResponse {
  const params = new URLSearchParams();
  const source = request.nextUrl.searchParams;
  for (const key of STAFF_LIST_FILTERS) {
    const value = source.get(key);
    if (value) params.set(key, value);
  }
  const limits = source.getAll("limit");
  if (limits.length > 1) return invalidStaffListQuery("limit");
  const limit = limits[0];
  if (limit) {
    if (!STAFF_LIST_LIMIT.test(limit)) return invalidStaffListQuery("limit");
    params.set("limit", limit);
  }
  const cursors = source.getAll("after");
  if (cursors.length > 1) return invalidStaffListQuery("after");
  const after = cursors[0];
  if (after) {
    if (!STAFF_LIST_CURSOR.test(after)) return invalidStaffListQuery("after");
    params.set("after", after);
  }
  const query = params.size ? `?${params.toString()}` : "";
  return authenticatedGet(request, `/api/v1/staff/${query}`);
}

export function getTenantStaff(
  request: NextRequest,
  staffId: string,
): Promise<NextResponse> {
  return authenticatedGet(request, `/api/v1/staff/${staffId}/`);
}

export function updateTenantStaff(
  request: NextRequest,
  staffId: string,
): Promise<NextResponse> {
  return authenticatedMutation(request, `/api/v1/staff/${staffId}/`, "PATCH");
}

export function inviteTenantStaff(request: NextRequest): Promise<NextResponse> {
  return authenticatedMutation(request, "/api/v1/staff/invitations/", "POST");
}

export function activateTenantStaff(
  request: NextRequest,
  staffId: string,
): Promise<NextResponse> {
  return authenticatedMutation(
    request,
    `/api/v1/staff/${staffId}/activate/`,
    "POST",
  );
}

export function suspendTenantStaff(
  request: NextRequest,
  staffId: string,
): Promise<NextResponse> {
  return authenticatedMutation(
    request,
    `/api/v1/staff/${staffId}/suspend/`,
    "POST",
  );
}

function invalidCollectionQuery(field: "limit" | "after"): NextResponse {
  const message =
    field === "limit" ? "Limit must be from 1 to 100." : "Invalid cursor.";
  return responseFromUpstream(400, {
    success: false,
    error: {
      code: "INVALID_REQUEST",
      message,
      fields: { [field]: [message] },
    },
  });
}

function collectionPath(
  request: NextRequest,
  pathname: string,
): string | NextResponse {
  const params = new URLSearchParams();
  const source = request.nextUrl.searchParams;
  const limits = source.getAll("limit");
  if (limits.length > 1) return invalidCollectionQuery("limit");
  const limit = limits[0];
  if (limit) {
    if (!STAFF_LIST_LIMIT.test(limit)) return invalidCollectionQuery("limit");
    params.set("limit", limit);
  }
  const cursors = source.getAll("after");
  if (cursors.length > 1) return invalidCollectionQuery("after");
  const after = cursors[0];
  if (after) {
    if (!STAFF_LIST_CURSOR.test(after)) return invalidCollectionQuery("after");
    params.set("after", after);
  }
  const query = params.size ? `?${params.toString()}` : "";
  return `${pathname}${query}`;
}

export function unknownRoleResponse(): NextResponse {
  return NextResponse.json(
    {
      success: false,
      error: { code: "NOT_FOUND", message: "Role was not found." },
    },
    { status: 404, headers: { "Cache-Control": "no-store, private" } },
  );
}

export function unknownMembershipResponse(): NextResponse {
  return NextResponse.json(
    {
      success: false,
      error: { code: "NOT_FOUND", message: "Membership was not found." },
    },
    { status: 404, headers: { "Cache-Control": "no-store, private" } },
  );
}

export function listTenantRoles(
  request: NextRequest,
): Promise<NextResponse> | NextResponse {
  const path = collectionPath(request, "/api/v1/roles/");
  if (typeof path !== "string") return path;
  return authenticatedGet(request, path);
}

export function createTenantRole(request: NextRequest): Promise<NextResponse> {
  return authenticatedMutation(request, "/api/v1/roles/", "POST");
}

export function getTenantRole(
  request: NextRequest,
  roleId: string,
): Promise<NextResponse> {
  return authenticatedGet(request, `/api/v1/roles/${roleId}/`);
}

export function updateTenantRole(
  request: NextRequest,
  roleId: string,
): Promise<NextResponse> {
  return authenticatedMutation(request, `/api/v1/roles/${roleId}/`, "PATCH");
}

export function archiveTenantRole(
  request: NextRequest,
  roleId: string,
): Promise<NextResponse> {
  return authenticatedMutation(
    request,
    `/api/v1/roles/${roleId}/archive/`,
    "POST",
  );
}

export function replaceTenantRolePermissions(
  request: NextRequest,
  roleId: string,
): Promise<NextResponse> {
  return authenticatedMutation(
    request,
    `/api/v1/roles/${roleId}/permissions/`,
    "PUT",
  );
}

export function listTenantPermissions(
  request: NextRequest,
): Promise<NextResponse> | NextResponse {
  const path = collectionPath(request, "/api/v1/permissions/");
  if (typeof path !== "string") return path;
  return authenticatedGet(request, path);
}

export function getMembershipRoles(
  request: NextRequest,
  membershipId: string,
): Promise<NextResponse> {
  return authenticatedGet(
    request,
    `/api/v1/gym-memberships/${membershipId}/roles/`,
  );
}

export function replaceMembershipRoles(
  request: NextRequest,
  membershipId: string,
): Promise<NextResponse> {
  return authenticatedMutation(
    request,
    `/api/v1/gym-memberships/${membershipId}/roles/`,
    "PUT",
  );
}

const TENANT_AUDIT_STRING_FIELDS = [
  "id",
  "action",
  "module",
  "target_type",
  "target_id",
  "request_id",
  "outcome",
  "occurred_at",
] as const;
const ERROR_CODE_PATTERN = /^[A-Za-z0-9_.-]{1,64}$/;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const TENANT_AUDIT_SUCCESS_MESSAGE = "Success";
const TENANT_AUDIT_PUBLIC_ERROR_MESSAGE = "The request could not be completed.";

function redactExactSecretKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactExactSecretKeys);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [
      key,
      SECRET_KEYS.has(key) ? "[REDACTED]" : redactExactSecretKeys(child),
    ]),
  );
}

function redactTenantAuditPayload(value: unknown): unknown {
  return redactExactSecretKeys(redactTenantAuditValue(value));
}

function copyContractString(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length === 0 || value.length > 512) {
    return undefined;
  }
  return value;
}

function projectTenantAuditItem(row: JsonRecord): JsonRecord {
  const projected: JsonRecord = {};
  for (const key of TENANT_AUDIT_STRING_FIELDS) {
    const value = copyContractString(row[key]);
    if (value !== undefined) projected[key] = value;
  }
  for (const key of [
    "actor_membership_id",
    "impersonator_membership_id",
  ] as const) {
    const value = row[key];
    if (value === null) projected[key] = null;
    else {
      const copied = copyContractString(value);
      if (copied !== undefined) projected[key] = copied;
    }
  }
  if ("old_values" in row) projected.old_values = row.old_values;
  if ("new_values" in row) projected.new_values = row.new_values;
  return projected;
}

function projectTenantAuditMeta(value: unknown): JsonRecord {
  const meta = isRecord(value) ? value : {};
  const nextAfter = meta.next_after;
  const projected: JsonRecord = {
    has_more: meta.has_more === true,
    next_after:
      typeof nextAfter === "string" &&
      nextAfter.length > 0 &&
      nextAfter.length <= 2048
        ? nextAfter
        : null,
  };
  if (meta.scope === "GYM" || meta.scope === "SELF")
    projected.scope = meta.scope;
  return projected;
}

function projectTenantAuditSuccess(body: JsonRecord): unknown {
  const projected: JsonRecord = {
    success: true,
    data: Array.isArray(body.data)
      ? body.data.filter(isRecord).map(projectTenantAuditItem)
      : [],
    meta: projectTenantAuditMeta(body.meta),
  };
  if (body.message === TENANT_AUDIT_SUCCESS_MESSAGE) {
    projected.message = TENANT_AUDIT_SUCCESS_MESSAGE;
  }
  return redactTenantAuditPayload(projected);
}

function presentTenantAuditError(body: unknown): ApiErrorBody {
  const requestId =
    isRecord(body) &&
    typeof body.request_id === "string" &&
    REQUEST_ID_PATTERN.test(body.request_id)
      ? body.request_id
      : undefined;
  const code =
    isRecord(body) && isRecord(body.error) ? body.error.code : undefined;
  const projected: ApiErrorBody = {
    success: false,
    error: {
      code:
        typeof code === "string" && ERROR_CODE_PATTERN.test(code)
          ? code
          : "INVALID_UPSTREAM_RESPONSE",
      message: TENANT_AUDIT_PUBLIC_ERROR_MESSAGE,
    },
    ...(requestId ? { request_id: requestId } : {}),
  };
  return projected;
}

function presentTenantAuditBody(status: number, body: unknown): unknown {
  if (
    status >= 200 &&
    status < 300 &&
    isRecord(body) &&
    body.success !== false &&
    Array.isArray(body.data)
  ) {
    return projectTenantAuditSuccess(body);
  }
  if (status >= 200 && status < 300) {
    throw new Error("Invalid tenant audit response.");
  }
  return presentTenantAuditError(body);
}

export function listTenantAuditLogs(
  request: NextRequest,
): Promise<NextResponse> {
  return authenticatedGet(
    request,
    `/api/v1/audit-logs/${request.nextUrl.search}`,
    presentTenantAuditBody,
  );
}

export async function serverAuthMe(accessToken: string): Promise<Response> {
  const existing = bffContext.getStore();
  const requestId =
    existing?.requestId ??
    resolveRequestId((await nextHeaders()).get("x-request-id"));
  const send = () =>
    callDjango("/api/v1/auth/me/", {
      method: "GET",
      headers: { Authorization: `Bearer ${accessToken}` },
    });
  const load = async () => {
    try {
      const response = await send();
      if (
        response.status === 401 ||
        response.status === 403 ||
        response.status >= 500
      ) {
        logBffFailure({
          requestId,
          route: "/api/v1/auth/me/",
          method: "GET",
          status: response.status,
          outcome:
            response.status >= 500 ? "upstream_error" : "authentication_failed",
        });
      }
      return response;
    } catch (error) {
      logBffFailure({
        requestId,
        route: "/api/v1/auth/me/",
        method: "GET",
        status: 503,
        outcome: "upstream_unavailable",
      });
      throw error;
    }
  };
  if (existing) return load();
  return bffContext.run(
    {
      requestId,
      method: "GET",
      route: normalizedRoute("/api/v1/auth/me/"),
    },
    load,
  );
}

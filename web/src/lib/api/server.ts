import "server-only";

import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  AUTH_COOKIES,
  SELECTION_COOKIE_MAX_AGE,
  authCookieOptions,
  clearCookieOptions,
} from "@/lib/auth/cookies";
import { verifyMutationRequest } from "@/lib/auth/csrf";
import { getServerEnv } from "@/lib/env";
import type { ApiErrorBody } from "@/types/api";

const ALLOWED_PATHS = new Set([
  "/api/v1/auth/login/",
  "/api/v1/auth/refresh/",
  "/api/v1/auth/logout/",
  "/api/v1/auth/logout-all/",
  "/api/v1/auth/me/",
  "/api/v1/auth/gyms/",
  "/api/v1/auth/select-gym/",
]);

const ALLOWED_TENANT_PATHS = [
  /^\/api\/v1\/members\/(?:[0-9a-f-]+\/(?:summary\/|archive\/)?)?$/,
  /^\/api\/v1\/membership-plans\/(?:[0-9a-f-]+\/(?:archive\/)?)?$/,
  /^\/api\/v1\/memberships\/(?:expiry\/|[0-9a-f-]+\/(?:cancel|suspend|resume)\/)?$/,
  /^\/api\/v1\/payments\/(?:[0-9a-f-]+\/(?:submit|approve|reject|cancel|void|refund)\/)?$/,
  /^\/api\/v1\/dashboard\/$/,
];

const SECRET_KEYS = new Set([
  "access_token",
  "refresh_token",
  "token",
  "refresh_session",
  "gym_selection_ticket",
]);

const INBOUND_SECRET_KEYS = new Set([
  "access_token",
  "refresh_token",
  "token",
  "refresh_session",
  "gym_selection_ticket",
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

async function callDjango(path: string, init: RequestInit): Promise<Response> {
  const pathname = new URL(path, "http://internal").pathname;
  const allowed =
    ALLOWED_PATHS.has(pathname) ||
    ALLOWED_TENANT_PATHS.some((pattern) => pattern.test(pathname));
  if (!allowed) throw new Error("Django route is not allowed.");
  const { djangoApiUrl, bffSharedSecret } = getServerEnv();
  return fetch(`${djangoApiUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      [BFF_SECRET_HEADER]: bffSharedSecret,
      ...init.headers,
    },
  });
}

function responseFromUpstream(status: number, body: unknown): NextResponse {
  const headers = { "Cache-Control": "no-store, private" };
  if (status === 204) return new NextResponse(null, { status, headers });
  if (body === null) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "INVALID_UPSTREAM_RESPONSE",
          message: "The service returned an invalid response.",
        },
      },
      { status: 502, headers },
    );
  }
  return NextResponse.json(stripCredentials(body), { status, headers });
}

function setTokenCookies(response: NextResponse, body: unknown): void {
  const { secureCookies } = getServerEnv();
  const access = findString(body, "access_token");
  const refresh = findString(body, "refresh_token");
  const ticket = findString(body, "gym_selection_ticket");
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

export async function authMutation(
  request: NextRequest,
  path: string,
  body: JsonRecord,
  options: {
    includeAccess?: boolean;
    includeRefresh?: boolean;
    includeSelectionTicket?: boolean;
    clearAfter?: boolean;
  } = {},
): Promise<NextResponse> {
  const env = getServerEnv();
  const verified = verifyMutationRequest(request, env.allowedOrigins);
  if (!verified.ok) {
    return NextResponse.json(
      {
        success: false,
        error: { code: "CSRF_FAILED", message: verified.message },
      },
      {
        status: 403,
        headers: { "Cache-Control": "no-store, private" },
      },
    );
  }

  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  const refresh = request.cookies.get(AUTH_COOKIES.refresh)?.value;
  const selectionTicket = request.cookies.get(
    AUTH_COOKIES.selectionTicket,
  )?.value;
  const upstreamBody = {
    ...stripInboundSecrets(body),
    ...(options.includeRefresh && refresh ? { refresh_token: refresh } : {}),
    ...(options.includeSelectionTicket && selectionTicket
      ? { gym_selection_ticket: selectionTicket }
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
    const response = NextResponse.json(
      {
        success: false,
        error: {
          code: "UPSTREAM_UNAVAILABLE",
          message: "The service is temporarily unavailable. Please try again.",
        },
      },
      {
        status: 503,
        headers: { "Cache-Control": "no-store, private" },
      },
    );
    if (options.clearAfter) clearAuthCookies(response);
    return response;
  }
}

function errorCode(body: unknown): string | undefined {
  if (!isRecord(body) || !isRecord(body.error)) return undefined;
  return typeof body.error.code === "string" ? body.error.code : undefined;
}

export async function authenticatedGet(
  request: NextRequest,
  path: string,
): Promise<NextResponse> {
  const access = request.cookies.get(AUTH_COOKIES.access)?.value;
  if (!access) {
    return responseFromUpstream(401, {
      success: false,
      error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in." },
    });
  }

  try {
    let upstream = await callDjango(path, {
      method: "GET",
      headers: { Authorization: `Bearer ${access}` },
    });
    let body = await parseJson(upstream);
    const env = getServerEnv();
    if (upstream.status === 401 && errorCode(body) === env.accessExpiredCode) {
      const verified = verifyMutationRequest(request, env.allowedOrigins);
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
      if (!newAccess) return responseFromUpstream(502, null);

      upstream = await callDjango(path, {
        method: "GET",
        headers: { Authorization: `Bearer ${newAccess}` },
      });
      body = await parseJson(upstream);
      const response = responseFromUpstream(upstream.status, body);
      setTokenCookies(response, refreshedBody);
      return response;
    }
    return responseFromUpstream(upstream.status, body);
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

export async function authenticatedMutation(
  request: NextRequest,
  path: string,
  method: "POST" | "PATCH" = "POST",
): Promise<NextResponse> {
  const body = await parseRequestBody(request);
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
  try {
    const upstream = await callDjango(path, {
      method,
      headers: {
        Authorization: `Bearer ${access}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
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

export async function serverAuthMe(accessToken: string): Promise<Response> {
  return callDjango("/api/v1/auth/me/", {
    method: "GET",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

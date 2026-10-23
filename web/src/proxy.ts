import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  AUTH_COOKIES,
  authCookieOptions,
  clearCookieOptions,
} from "@/lib/auth/cookies";
import { getServerEnv } from "@/lib/env";

type JsonRecord = Record<string, unknown>;
type RefreshResult = {
  access: string | undefined;
  accessExpiresAt: string | undefined;
  refresh: string | undefined;
  refreshExpiresAt: string | undefined;
  status: number;
};

const BFF_SECRET_HEADER = "X-Gym-BFF-Secret";
const REFRESH_RESULT_GRACE_MS = 5000;
const refreshRequests = new Map<
  string,
  { expiresAt: number; promise: Promise<RefreshResult> }
>();

function findString(value: unknown, key: string): string | undefined {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = findString(child, key);
      if (found) return found;
    }
  } else if (typeof value === "object" && value !== null) {
    const record = value as JsonRecord;
    if (typeof record[key] === "string") return record[key];
    for (const child of Object.values(record)) {
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

function clearStaleSession(response: NextResponse, secure: boolean): void {
  response.cookies.set(
    AUTH_COOKIES.access,
    "",
    clearCookieOptions(secure, true),
  );
  response.cookies.set(
    AUTH_COOKIES.refresh,
    "",
    clearCookieOptions(secure, true),
  );
}

function refreshSession(
  refresh: string,
  requestId: string,
  env: ReturnType<typeof getServerEnv>,
): Promise<RefreshResult> {
  const now = Date.now();
  const existing = refreshRequests.get(refresh);
  if (existing && existing.expiresAt > now) return existing.promise;

  if (refreshRequests.size >= 100) {
    for (const [token, entry] of refreshRequests) {
      if (entry.expiresAt <= now) refreshRequests.delete(token);
    }
  }

  const promise = fetch(`${env.djangoApiUrl}/api/v1/auth/refresh/`, {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      [BFF_SECRET_HEADER]: env.bffSharedSecret,
      "X-Request-ID": requestId,
      ...(env.secureCookies ? { "X-Forwarded-Proto": "https" } : {}),
    },
    body: JSON.stringify({ refresh_token: refresh }),
  }).then(async (upstream) => {
    const body: unknown = await upstream.json().catch(() => null);
    return {
      status: upstream.status,
      access: findString(body, "access_token"),
      refresh: findString(body, "refresh_token"),
      accessExpiresAt: findString(body, "access_expires_at"),
      refreshExpiresAt: findString(body, "refresh_expires_at"),
    };
  });
  refreshRequests.set(refresh, {
    expiresAt: now + REFRESH_RESULT_GRACE_MS,
    promise,
  });
  return promise;
}

export async function proxy(request: NextRequest) {
  if (request.method !== "GET" || request.cookies.has(AUTH_COOKIES.access)) {
    return NextResponse.next();
  }

  const refresh = request.cookies.get(AUTH_COOKIES.refresh)?.value;
  if (!refresh) return NextResponse.next();

  const env = getServerEnv();
  try {
    const refreshed = await refreshSession(
      refresh,
      request.headers.get("x-request-id") ?? crypto.randomUUID(),
      env,
    );

    if (
      refreshed.status < 200 ||
      refreshed.status >= 300 ||
      !refreshed.access ||
      !refreshed.refresh
    ) {
      const response = NextResponse.next();
      if (refreshed.status === 400 || refreshed.status === 401) {
        clearStaleSession(response, env.secureCookies);
      }
      return response;
    }

    request.cookies.set(AUTH_COOKIES.access, refreshed.access);
    request.cookies.set(AUTH_COOKIES.refresh, refreshed.refresh);
    const response = NextResponse.next({
      request: { headers: new Headers(request.headers) },
    });
    response.cookies.set(
      AUTH_COOKIES.access,
      refreshed.access,
      authCookieOptions(env.secureCookies, maxAge(refreshed.accessExpiresAt)),
    );
    response.cookies.set(
      AUTH_COOKIES.refresh,
      refreshed.refresh,
      authCookieOptions(env.secureCookies, maxAge(refreshed.refreshExpiresAt)),
    );
    return response;
  } catch {
    return NextResponse.next();
  }
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};

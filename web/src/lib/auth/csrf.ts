import { timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

import { AUTH_COOKIES } from "@/lib/auth/cookies";

export const CSRF_HEADER = "x-csrf-token";

function equalTokens(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function verifyMutationRequest(
  request: NextRequest,
  allowedOrigins: ReadonlySet<string>,
): { ok: true } | { ok: false; message: string } {
  const cookieToken = request.cookies.get(AUTH_COOKIES.csrf)?.value;
  const headerToken = request.headers.get(CSRF_HEADER);
  if (
    !cookieToken ||
    !headerToken ||
    cookieToken.length < 32 ||
    !equalTokens(cookieToken, headerToken)
  ) {
    return { ok: false, message: "Invalid CSRF token." };
  }

  const origin = request.headers.get("origin");
  if (origin) {
    return allowedOrigins.has(origin)
      ? { ok: true }
      : { ok: false, message: "Untrusted request origin." };
  }

  const referer = request.headers.get("referer");
  if (!referer) return { ok: false, message: "Missing request provenance." };
  try {
    return allowedOrigins.has(new URL(referer).origin)
      ? { ok: true }
      : { ok: false, message: "Untrusted request referer." };
  } catch {
    return { ok: false, message: "Malformed request referer." };
  }
}

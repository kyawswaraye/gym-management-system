import { randomBytes } from "node:crypto";
import { NextResponse } from "next/server";

import { AUTH_COOKIES, csrfCookieOptions } from "@/lib/auth/cookies";
import { getServerEnv } from "@/lib/env";

export function GET(): NextResponse {
  const token = randomBytes(32).toString("base64url");
  const response = NextResponse.json(
    { success: true, data: { csrf_token: token } },
    { headers: { "Cache-Control": "no-store, private" } },
  );
  response.cookies.set(
    AUTH_COOKIES.csrf,
    token,
    csrfCookieOptions(getServerEnv().secureCookies),
  );
  return response;
}

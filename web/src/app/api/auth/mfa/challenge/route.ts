import type { NextRequest } from "next/server";

import { authMutation, parseRequestBody } from "@/lib/api/server";

export async function POST(request: NextRequest) {
  const body = await parseRequestBody(request);
  return authMutation(request, "/api/v1/auth/mfa/challenge/", body ?? {}, {
    includeMfaChallenge: true,
  });
}

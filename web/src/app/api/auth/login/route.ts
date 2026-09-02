import type { NextRequest } from "next/server";

import { authMutation, parseRequestBody } from "@/lib/api/server";
import { safeErrorResponse } from "@/lib/api/errors";

export async function POST(request: NextRequest) {
  const body = await parseRequestBody(request);
  if (!body) {
    return safeErrorResponse(400, "INVALID_REQUEST", "Invalid request body.");
  }
  return authMutation(request, "/api/v1/auth/login/", body);
}

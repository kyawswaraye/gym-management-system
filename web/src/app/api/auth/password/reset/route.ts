import type { NextRequest } from "next/server";

import { safeErrorResponse } from "@/lib/api/errors";
import { authMutation, parseRequestBody } from "@/lib/api/server";
import { resolveRequestId } from "@/lib/log/structured";

export async function POST(request: NextRequest) {
  const body = await parseRequestBody(request);
  if (!body) {
    return safeErrorResponse(
      400,
      "INVALID_REQUEST",
      "Invalid request body.",
      resolveRequestId(request.headers.get("x-request-id")),
    );
  }
  return authMutation(request, "/api/v1/auth/password/reset/", body);
}

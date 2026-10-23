import type { NextRequest } from "next/server";

import { safeErrorResponse } from "@/lib/api/errors";
import { authMutation, parseRequestBody } from "@/lib/api/server";
import { AUTH_COOKIES } from "@/lib/auth/cookies";
import { resolveRequestId } from "@/lib/log/structured";

export async function POST(request: NextRequest) {
  const body = await parseRequestBody(request);
  if (!body || typeof body.gym_membership_id !== "string") {
    return safeErrorResponse(
      400,
      "INVALID_GYM_SELECTION",
      "Choose a valid gym membership.",
      resolveRequestId(request.headers.get("x-request-id")),
    );
  }
  const ticket = request.cookies.get(AUTH_COOKIES.selectionTicket)?.value;
  return authMutation(request, "/api/v1/auth/select-gym/", body, {
    includeAccess: !ticket,
    includeSelectionTicket: Boolean(ticket),
  });
}

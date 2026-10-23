import { NextResponse } from "next/server";

import type { ApiErrorBody } from "@/types/api";

export function safeErrorResponse(
  status: number,
  code: string,
  message: string,
  requestId?: string,
): NextResponse<ApiErrorBody> {
  const body: ApiErrorBody = {
    success: false,
    error: { code, message },
    ...(requestId ? { request_id: requestId } : {}),
  };
  const headers: Record<string, string> = {
    "Cache-Control": "no-store, private",
  };
  if (requestId) headers["X-Request-ID"] = requestId;
  return NextResponse.json(body, {
    status,
    headers,
  });
}

export function upstreamUnavailable(): NextResponse<ApiErrorBody> {
  return safeErrorResponse(
    503,
    "UPSTREAM_UNAVAILABLE",
    "The service is temporarily unavailable. Please try again.",
  );
}

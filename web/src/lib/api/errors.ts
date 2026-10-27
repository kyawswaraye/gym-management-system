import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE, parseLocale, type Locale } from "@/i18n/locale";
import { translate } from "@/i18n/translate";
import type { ApiErrorBody } from "@/types/api";

export function requestLocale(request: NextRequest): Locale {
  return parseLocale(request.cookies.get(LOCALE_COOKIE)?.value);
}

export function safeErrorResponse(
  status: number,
  code: string,
  message: string,
  requestId?: string,
  locale: Locale = "en",
): NextResponse<ApiErrorBody> {
  const body: ApiErrorBody = {
    success: false,
    error: { code, message: translate(locale, message) },
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

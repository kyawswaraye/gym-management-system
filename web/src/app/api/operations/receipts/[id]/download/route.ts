import { NextResponse, type NextRequest } from "next/server";

import { translate } from "@/i18n/translate";
import { requestLocale } from "@/lib/api/errors";
import {
  authenticatedDocumentGet,
  normalizeResourceId,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

export async function GET(request: NextRequest, context: Context) {
  if (request.nextUrl.search.length > 0) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "INVALID_REQUEST",
          message: translate(requestLocale(request), "Invalid request."),
        },
      },
      { status: 400, headers: { "Cache-Control": "no-store, private" } },
    );
  }
  const { id } = await context.params;
  const receiptId = normalizeResourceId(id);
  if (!receiptId) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "NOT_FOUND",
          message: translate(requestLocale(request), "Receipt was not found."),
        },
      },
      { status: 404, headers: { "Cache-Control": "no-store, private" } },
    );
  }
  return authenticatedDocumentGet(
    request,
    `/api/v1/receipts/${receiptId}/download/`,
  );
}

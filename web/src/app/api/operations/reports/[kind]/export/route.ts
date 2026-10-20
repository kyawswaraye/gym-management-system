import { NextResponse, type NextRequest } from "next/server";

import { allowsReportExport } from "@/lib/api/reports-routes";
import { authenticatedDocumentGet } from "@/lib/api/server";

const KIND =
  /^(?:daily|monthly|cash-book|profit-loss|product-sales|membership-expiry|check-ins|pt-sessions|classes|stock-movements|inventory)$/;

type Context = { params: Promise<{ kind: string }> };

function rejected(status: number, code: string, message: string) {
  return NextResponse.json(
    { success: false, error: { code, message } },
    { status, headers: { "Cache-Control": "no-store, private" } },
  );
}

export async function GET(request: NextRequest, context: Context) {
  const { kind } = await context.params;
  if (!KIND.test(kind)) {
    return rejected(404, "NOT_FOUND", "Report export was not found.");
  }
  const pathname = `/api/v1/reports/${kind}/export/`;
  if (!allowsReportExport(pathname, request.nextUrl.search, "GET")) {
    return rejected(400, "INVALID_REQUEST", "Invalid request.");
  }
  return authenticatedDocumentGet(
    request,
    `${pathname}${request.nextUrl.search}`,
    "csv",
  );
}

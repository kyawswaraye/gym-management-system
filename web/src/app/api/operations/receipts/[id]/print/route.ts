import type { NextRequest } from "next/server";

import { getReceiptPrintPayload } from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

export async function GET(request: NextRequest, context: Context) {
  const { id } = await context.params;
  return getReceiptPrintPayload(request, id);
}

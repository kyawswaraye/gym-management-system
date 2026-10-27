import type { NextRequest } from "next/server";

import { getReceiptPrintContext } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return getReceiptPrintContext(request);
}

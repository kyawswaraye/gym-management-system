import type { NextRequest } from "next/server";

import { listTenantAuditLogs } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return listTenantAuditLogs(request);
}

import type { NextRequest } from "next/server";

import { listTenantPermissions } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return listTenantPermissions(request);
}

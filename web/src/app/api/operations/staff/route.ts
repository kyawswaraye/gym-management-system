import type { NextRequest } from "next/server";

import { listTenantStaff } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return listTenantStaff(request);
}

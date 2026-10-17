import type { NextRequest } from "next/server";

import { createTenantRole, listTenantRoles } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return listTenantRoles(request);
}

export function POST(request: NextRequest) {
  return createTenantRole(request);
}

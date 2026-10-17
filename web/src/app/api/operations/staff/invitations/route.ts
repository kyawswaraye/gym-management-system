import type { NextRequest } from "next/server";

import { inviteTenantStaff } from "@/lib/api/server";

export function POST(request: NextRequest) {
  return inviteTenantStaff(request);
}

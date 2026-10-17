import type { NextRequest } from "next/server";

import { getTenantGymProfile, updateTenantGymProfile } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return getTenantGymProfile(request);
}

export function PATCH(request: NextRequest) {
  return updateTenantGymProfile(request);
}

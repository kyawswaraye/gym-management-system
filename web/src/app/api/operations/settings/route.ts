import type { NextRequest } from "next/server";

import {
  getTenantGymSettings,
  updateTenantGymSettings,
} from "@/lib/api/server";

export function GET(request: NextRequest) {
  return getTenantGymSettings(request);
}

export function PATCH(request: NextRequest) {
  return updateTenantGymSettings(request);
}

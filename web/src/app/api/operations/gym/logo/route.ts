import type { NextRequest } from "next/server";

import {
  deleteTenantGymLogo,
  getTenantGymLogo,
  updateTenantGymLogo,
} from "@/lib/api/server";

export function GET(request: NextRequest) {
  return getTenantGymLogo(request);
}

export function PUT(request: NextRequest) {
  return updateTenantGymLogo(request);
}

export function DELETE(request: NextRequest) {
  return deleteTenantGymLogo(request);
}

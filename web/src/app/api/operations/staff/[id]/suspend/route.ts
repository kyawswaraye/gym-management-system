import type { NextRequest } from "next/server";

import {
  normalizeStaffId,
  suspendTenantStaff,
  unknownStaffResponse,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

export async function POST(request: NextRequest, context: Context) {
  const id = normalizeStaffId((await context.params).id);
  if (!id) return unknownStaffResponse(request);
  return suspendTenantStaff(request, id);
}

import type { NextRequest } from "next/server";

import {
  getTenantStaff,
  normalizeStaffId,
  unknownStaffResponse,
  updateTenantStaff,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

async function staffId(context: Context) {
  return normalizeStaffId((await context.params).id);
}

export async function GET(request: NextRequest, context: Context) {
  const id = await staffId(context);
  if (!id) return unknownStaffResponse(request);
  return getTenantStaff(request, id);
}

export async function PATCH(request: NextRequest, context: Context) {
  const id = await staffId(context);
  if (!id) return unknownStaffResponse(request);
  return updateTenantStaff(request, id);
}

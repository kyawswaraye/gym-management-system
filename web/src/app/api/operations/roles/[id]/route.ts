import type { NextRequest } from "next/server";

import {
  getTenantRole,
  normalizeResourceId,
  unknownRoleResponse,
  updateTenantRole,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

async function roleId(context: Context) {
  return normalizeResourceId((await context.params).id);
}

export async function GET(request: NextRequest, context: Context) {
  const id = await roleId(context);
  if (!id) return unknownRoleResponse(request);
  return getTenantRole(request, id);
}

export async function PATCH(request: NextRequest, context: Context) {
  const id = await roleId(context);
  if (!id) return unknownRoleResponse(request);
  return updateTenantRole(request, id);
}

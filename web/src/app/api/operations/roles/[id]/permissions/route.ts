import type { NextRequest } from "next/server";

import {
  normalizeResourceId,
  replaceTenantRolePermissions,
  unknownRoleResponse,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

export async function PUT(request: NextRequest, context: Context) {
  const id = normalizeResourceId((await context.params).id);
  if (!id) return unknownRoleResponse();
  return replaceTenantRolePermissions(request, id);
}

import type { NextRequest } from "next/server";

import {
  archiveTenantRole,
  normalizeResourceId,
  unknownRoleResponse,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

export async function POST(request: NextRequest, context: Context) {
  const id = normalizeResourceId((await context.params).id);
  if (!id) return unknownRoleResponse(request);
  return archiveTenantRole(request, id);
}

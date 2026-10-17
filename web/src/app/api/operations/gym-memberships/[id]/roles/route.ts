import type { NextRequest } from "next/server";

import {
  getMembershipRoles,
  normalizeResourceId,
  replaceMembershipRoles,
  unknownMembershipResponse,
} from "@/lib/api/server";

type Context = { params: Promise<{ id: string }> };

async function membershipId(context: Context) {
  return normalizeResourceId((await context.params).id);
}

export async function GET(request: NextRequest, context: Context) {
  const id = await membershipId(context);
  if (!id) return unknownMembershipResponse();
  return getMembershipRoles(request, id);
}

export async function PUT(request: NextRequest, context: Context) {
  const id = await membershipId(context);
  if (!id) return unknownMembershipResponse();
  return replaceMembershipRoles(request, id);
}

import type { NextRequest } from "next/server";

import { authenticatedGet, authenticatedMutation } from "@/lib/api/server";

type Context = { params: Promise<{ path: string[] }> };

async function djangoPath(request: NextRequest, context: Context) {
  const { path } = await context.params;
  const base = `/api/v1/${path.join("/")}/`;
  return `${base}${request.nextUrl.search}`;
}

export async function GET(request: NextRequest, context: Context) {
  return authenticatedGet(request, await djangoPath(request, context));
}

export async function POST(request: NextRequest, context: Context) {
  return authenticatedMutation(
    request,
    await djangoPath(request, context),
    "POST",
  );
}

export async function PATCH(request: NextRequest, context: Context) {
  return authenticatedMutation(
    request,
    await djangoPath(request, context),
    "PATCH",
  );
}

export async function PUT(request: NextRequest, context: Context) {
  return authenticatedMutation(
    request,
    await djangoPath(request, context),
    "PUT",
  );
}

export async function DELETE(request: NextRequest, context: Context) {
  return authenticatedMutation(
    request,
    await djangoPath(request, context),
    "DELETE",
  );
}

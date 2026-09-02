import type { NextRequest } from "next/server";

import { authenticatedGet } from "@/lib/api/server";

export function GET(request: NextRequest) {
  return authenticatedGet(request, "/api/v1/auth/me/");
}

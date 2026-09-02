import type { NextRequest } from "next/server";

import { authMutation } from "@/lib/api/server";

export function POST(request: NextRequest) {
  return authMutation(
    request,
    "/api/v1/auth/logout/",
    {},
    {
      includeAccess: true,
      includeRefresh: true,
      clearAfter: true,
    },
  );
}

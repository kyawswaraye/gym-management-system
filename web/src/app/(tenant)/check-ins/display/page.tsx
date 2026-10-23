import { redirect } from "next/navigation";

import {
  canIssueCheckInChallenge,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export default async function CheckInDisplayPage() {
  const session = await requireTenantSession();
  if (!canIssueCheckInChallenge(session)) {
    redirect(tenantHomePath(session));
  }
  redirect("/check-ins?view=qr");
}

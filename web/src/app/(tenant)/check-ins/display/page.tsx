import { redirect } from "next/navigation";

import { CheckInDisplay } from "@/features/operations/check-in-display";
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

  return (
    <section aria-labelledby="check-in-display-title">
      <h1 className="text-2xl font-bold" id="check-in-display-title">
        Check-in QR
      </h1>
      <p className="muted mb-6 mt-2">
        Show this rotating code at the desk. Members cannot scan it from a staff
        login.
      </p>
      <CheckInDisplay />
    </section>
  );
}

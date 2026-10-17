import { redirect } from "next/navigation";

import { CheckInsManager } from "@/features/operations/check-ins-manager";
import {
  canOpenCheckIns,
  hasPermission,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export default async function CheckInsPage() {
  const session = await requireTenantSession();
  if (!canOpenCheckIns(session)) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="check-ins-title">
      <h1 className="text-2xl font-bold" id="check-ins-title">
        Check-ins
      </h1>
      <p className="muted mb-6 mt-2">
        Search a member, record a manual visit, and check that visit out.
      </p>
      <CheckInsManager
        canCreate={hasPermission(session, "checkins.create")}
        canUpdate={hasPermission(session, "checkins.update")}
        timeZone={session.gym?.timezone ?? "Asia/Yangon"}
      />
    </section>
  );
}

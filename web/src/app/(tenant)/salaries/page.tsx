import { redirect } from "next/navigation";

import { SalariesManager } from "@/features/finance/salaries-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function SalariesPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "salaries.view", "finance")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="salaries-title">
      <h1 className="text-2xl font-bold" id="salaries-title">
        Salaries
      </h1>
      <p className="muted mb-6 mt-2">
        Enter each staff amount for the period. Posted periods are corrected
        only by a full void.
      </p>
      <SalariesManager
        canApprove={session.permissions.includes("salaries.approve")}
        canCreate={session.permissions.includes("salaries.create")}
        canSubmit={session.permissions.includes("salaries.submit")}
        canUpdate={session.permissions.includes("salaries.update")}
        canVoid={session.permissions.includes("salaries.void")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

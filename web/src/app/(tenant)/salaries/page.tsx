import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Save a draft for the pay period and submit it for approval. A posted period is corrected only by a full void."
        eyebrow="Finance"
        title="Salaries"
        titleId="salaries-title"
      />
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

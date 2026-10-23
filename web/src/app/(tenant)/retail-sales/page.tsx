import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { RetailSalesManager } from "@/features/finance/retail-sales-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function RetailSalesPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "retail.view", "finance")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="retail-sales-title">
      <PageHeader
        description="Build a draft sale and submit it for approval. A posted sale can be returned in part, or voided in full while nothing has been returned."
        eyebrow="Finance"
        title="Retail sales"
        titleId="retail-sales-title"
      />
      <RetailSalesManager
        canApprove={session.permissions.includes("retail.approve")}
        canCreate={session.permissions.includes("retail.create")}
        canSubmit={session.permissions.includes("retail.submit")}
        canUpdate={session.permissions.includes("retail.update")}
        canVoid={session.permissions.includes("retail.void")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

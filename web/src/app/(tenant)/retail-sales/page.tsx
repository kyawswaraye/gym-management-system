import { redirect } from "next/navigation";

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
      <h1 className="text-2xl font-bold" id="retail-sales-title">
        Retail sales
      </h1>
      <p className="muted mb-6 mt-2">
        Build a draft sale and submit it for approval. A posted sale is
        corrected by voiding the whole sale.
      </p>
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

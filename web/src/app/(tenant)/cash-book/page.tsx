import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { CashBookManager } from "@/features/finance/cash-book-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function CashBookPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "cashbook.view", "finance")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="cash-book-title">
      <PageHeader
        description="Open a register session, review the server totals, and close it when the counted cash is recorded."
        eyebrow="Finance"
        title="Cash book"
        titleId="cash-book-title"
      />
      <CashBookManager
        canAdjust={session.permissions.includes("cashbook.adjust")}
        canOpen={session.permissions.includes("cashbook.open")}
        canReconcile={session.permissions.includes("cashbook.reconcile")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

import { redirect } from "next/navigation";

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
      <h1 className="text-2xl font-bold" id="cash-book-title">
        Cash book
      </h1>
      <p className="muted mb-6 mt-2">
        Open a register session, review the server totals, and close it when the
        counted cash is recorded.
      </p>
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

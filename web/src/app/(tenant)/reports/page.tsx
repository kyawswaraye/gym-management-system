import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { ReportsManager } from "@/features/reports/reports-manager";
import {
  hasTenantCapability,
  tenantHomePath,
  trainerAssignmentRestricted,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Reports" };

export default async function ReportsPage() {
  const session = await requireTenantSession();
  if (
    !hasTenantCapability(session, "reports.view", "reports") ||
    trainerAssignmentRestricted(session)
  ) {
    redirect(tenantHomePath(session));
  }
  const canViewFinancial = hasTenantCapability(
    session,
    "reports.financial",
    "reports",
  );
  const canExport = hasTenantCapability(session, "reports.export", "reports");

  return (
    <section aria-labelledby="reports-title">
      <h1 className="text-3xl font-bold" id="reports-title">
        Reports
      </h1>
      <p className="muted mb-6 mt-2">
        Reports for the selected gym. Financial amounts are server totals.
        Product sales are financial. Stock movement, inventory, membership
        expiry, check-in, PT session, and class reports do not include money.
      </p>
      <ReportsManager
        canExport={canExport}
        canViewFinancial={canViewFinancial}
      />
    </section>
  );
}

import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Review operational trends and trusted server-calculated financial totals for your selected gym. Operational reports do not include money."
        eyebrow="Analytics workspace"
        title="Reports"
        titleId="reports-title"
      >
        <div className="mt-4 flex flex-wrap gap-2 text-xs font-medium text-slate-600">
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">
            Financial totals
          </span>
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">
            Member activity
          </span>
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">
            Inventory insights
          </span>
        </div>
      </PageHeader>
      <ReportsManager
        canExport={canExport}
        canViewFinancial={canViewFinancial}
      />
    </section>
  );
}

import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { OwnerDashboard } from "@/features/operations/owner-dashboard";
import {
  hasTenantCapability,
  tenantHomePath,
  trainerAssignmentRestricted,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Dashboard" };

export default async function DashboardPage() {
  const session = await requireTenantSession();
  if (
    !hasTenantCapability(session, "reports.view", "reports") ||
    trainerAssignmentRestricted(session)
  ) {
    const homePath = tenantHomePath(session);
    if (homePath !== "/dashboard") redirect(homePath);
    return (
      <section aria-labelledby="dashboard-title">
        <h1 className="text-3xl font-bold" id="dashboard-title">
          No workspace access
        </h1>
        <p className="muted mt-2">
          Your account has no enabled tenant feature with matching permission.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="dashboard-title">
      <h1 className="text-3xl font-bold" id="dashboard-title">
        Owner dashboard
      </h1>
      <p className="muted mb-6 mt-2">
        Operational totals for the selected period. Financial totals appear when
        this response includes them.
      </p>
      <OwnerDashboard />
    </section>
  );
}

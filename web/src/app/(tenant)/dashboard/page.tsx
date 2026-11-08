import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { OwnerDashboard } from "@/features/operations/owner-dashboard";
import {
  hasTenantCapability,
  tenantHomePath,
  trainerAssignmentRestricted,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export async function generateMetadata() {
  return pageMetadata("Dashboard");
}

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
        <PageHeader
          description="Your account has no enabled tenant feature with matching permission."
          eyebrow="Workspace"
          title="No workspace access"
          titleId="dashboard-title"
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="dashboard-title">
      <PageHeader
        actions={
          <div className="flex items-center gap-2 self-start rounded-full border border-primary-line bg-primary-soft px-3 py-1.5 text-caption font-semibold text-primary-hover sm:self-auto">
            <span
              aria-hidden="true"
              className="size-2 rounded-full bg-primary"
            />
            <Tx text="Live overview" />
          </div>
        }
        description="Monitor member activity, revenue, expenses, and the health of your gym from one place."
        title="Owner dashboard"
        titleId="dashboard-title"
      />
      <OwnerDashboard />
    </section>
  );
}

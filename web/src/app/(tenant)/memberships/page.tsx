import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { MembershipsManager } from "@/features/operations/memberships-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function MembershipsPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "memberships.view", "memberships")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="memberships-title">
      <PageHeader
        description="Manage plans, start memberships, and monitor expiry and payment status."
        eyebrow="Membership management"
        title="Memberships"
        titleId="memberships-title"
      />
      <MembershipsManager
        canCancel={session.permissions.includes("memberships.cancel")}
        canCreate={session.permissions.includes("memberships.create")}
        canUpdate={session.permissions.includes("memberships.update")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { StaffManager } from "@/features/operations/staff-manager";
import { staffAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  return pageMetadata("Staff");
}

export default async function StaffPage() {
  const session = await requireTenantSession();
  const access = staffAccess(session);
  if (!access.canView) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="staff-title">
      <PageHeader
        description="Look up a person, then update their profile or login access. Those two actions stay separate."
        eyebrow="Operations"
        title="Staff"
        titleId="staff-title"
      />
      <StaffManager {...access} />
    </section>
  );
}

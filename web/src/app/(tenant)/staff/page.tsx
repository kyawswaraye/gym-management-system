import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { StaffManager } from "@/features/operations/staff-manager";
import { staffAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Staff" };

export default async function StaffPage() {
  const session = await requireTenantSession();
  const access = staffAccess(session);
  if (!access.canView) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="staff-title">
      <PageHeader
        description="Review non-owner staff records for this gym. Access changes stay separate from profile edits."
        eyebrow="Operations"
        title="Staff"
        titleId="staff-title"
      />
      <StaffManager {...access} />
    </section>
  );
}

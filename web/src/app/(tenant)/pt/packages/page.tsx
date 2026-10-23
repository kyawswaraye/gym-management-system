import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PackagesManager } from "@/features/pt/packages-manager";
import {
  canOpenPtSchedule,
  hasPermission,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export default async function PackagesPage() {
  const session = await requireTenantSession();
  if (!canOpenPtSchedule(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="packages-title">
      <PageHeader
        description="Browse PT and class packages, session allowances, validity, and pricing in one place."
        eyebrow="Personal training"
        title="Packages"
        titleId="packages-title"
      />
      <PackagesManager
        canManage={hasPermission(session, "pt.schedule.manage")}
        currency={session.gym?.currency ?? ""}
      />
    </section>
  );
}

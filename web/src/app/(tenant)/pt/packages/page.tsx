import { redirect } from "next/navigation";

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
      <h1 className="text-2xl font-bold" id="packages-title">
        Packages
      </h1>
      <p className="muted mb-6 mt-2">
        Browse PT and class package definitions. A granted manage permission can
        create one. Definitions are not edited here.
      </p>
      <PackagesManager
        canManage={hasPermission(session, "pt.schedule.manage")}
        currency={session.gym?.currency ?? ""}
      />
    </section>
  );
}

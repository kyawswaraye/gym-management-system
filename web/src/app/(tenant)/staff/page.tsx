import type { Metadata } from "next";
import { redirect } from "next/navigation";

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
      <h1 id="staff-title" className="text-3xl font-bold">
        Staff
      </h1>
      <p className="muted mt-2">
        Review non-owner staff records for this gym. Access changes stay
        separate from profile edits.
      </p>
      <div className="mt-6">
        <StaffManager {...access} />
      </div>
    </section>
  );
}

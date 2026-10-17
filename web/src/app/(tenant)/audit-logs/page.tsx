import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { TenantAuditLog } from "@/features/operations/audit-log-manager";
import { hasPermission, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Audit logs" };

export default async function AuditLogsPage() {
  const session = await requireTenantSession();
  if (!hasPermission(session, "audit.view")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="audit-logs-title">
      <h1 id="audit-logs-title" className="text-3xl font-bold">
        Audit logs
      </h1>
      <p className="muted mt-2">
        Review redacted audit events available to your account.
      </p>
      <div className="mt-6">
        <TenantAuditLog canView />
      </div>
    </section>
  );
}

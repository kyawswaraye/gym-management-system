import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Review redacted audit events available to your account."
        eyebrow="Operations"
        title="Audit logs"
        titleId="audit-logs-title"
      />
      <TenantAuditLog canView />
    </section>
  );
}

import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { TenantAuditLog } from "@/features/operations/audit-log-manager";
import { hasPermission, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  return pageMetadata("Audit logs");
}

export default async function AuditLogsPage() {
  const session = await requireTenantSession();
  if (!hasPermission(session, "audit.view")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="audit-logs-title">
      <PageHeader
        description="Open an event to see who changed it and what changed. Sensitive values stay hidden."
        eyebrow="Operations"
        title="Audit logs"
        titleId="audit-logs-title"
      />
      <TenantAuditLog canView />
    </section>
  );
}

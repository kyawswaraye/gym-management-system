import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PlatformAuditLog } from "@/features/platform/audit-log-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Platform audit logs" };
export const dynamic = "force-dynamic";

export default async function SuperAdminAuditLogsPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.audit.view")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-audit-title">
      <PageHeader
        description="Review redacted platform audit events."
        eyebrow="Platform"
        title="Platform audit logs"
        titleId="platform-audit-title"
      />
      <PlatformAuditLog canView />
    </section>
  );
}

import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PlatformAuditLog } from "@/features/platform/audit-log-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export async function generateMetadata() {
  return pageMetadata("Audit logs");
}
export const dynamic = "force-dynamic";

export default async function SuperAdminAuditLogsPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.audit.view")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-audit-title">
      <PageHeader
        description="Newest events first. Open one to see who changed it and what changed. Sensitive values stay hidden."
        eyebrow="GymPilot MM"
        title="Audit logs"
        titleId="platform-audit-title"
      />
      <PlatformAuditLog canView />
    </section>
  );
}

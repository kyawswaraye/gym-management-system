import type { Metadata } from "next";
import { redirect } from "next/navigation";

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
      <h1 id="platform-audit-title" className="text-3xl font-bold">
        Platform audit logs
      </h1>
      <p className="muted mt-2">Review redacted platform audit events.</p>
      <div className="mt-6">
        <PlatformAuditLog canView />
      </div>
    </section>
  );
}

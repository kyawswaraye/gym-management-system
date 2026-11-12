import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { AnnouncementsManager } from "@/features/operations/announcements-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export default async function AnnouncementsPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "announcements.view", "members")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="announcements-title">
      <PageHeader
        description="Draft, publish, or cancel a notice for members."
        eyebrow="Member notices"
        title="Announcements"
        titleId="announcements-title"
      />
      <AnnouncementsManager
        canManage={session.permissions.includes("announcements.manage")}
      />
    </section>
  );
}

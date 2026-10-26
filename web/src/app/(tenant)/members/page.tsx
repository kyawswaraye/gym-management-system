import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { MembersManager } from "@/features/operations/members-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function MembersPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "members.view", "members")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="members-title">
      <PageHeader
        description="Search current members, or open Archived to find members who were archived."
        eyebrow="Member directory"
        title="Members"
        titleId="members-title"
      />
      <MembersManager
        canArchive={session.permissions.includes("members.archive")}
        canCreate={session.permissions.includes("members.create")}
        canPreviewMemberNumber={session.permissions.includes("settings.view")}
        canRestore={session.permissions.includes("members.restore")}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

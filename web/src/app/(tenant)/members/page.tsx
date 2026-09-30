import { redirect } from "next/navigation";

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
      <h1 className="text-2xl font-bold" id="members-title">
        Members
      </h1>
      <p className="muted mb-6 mt-2">
        Add, search, and manage members in this gym only.
      </p>
      <MembersManager
        canCreate={session.permissions.includes("members.create")}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

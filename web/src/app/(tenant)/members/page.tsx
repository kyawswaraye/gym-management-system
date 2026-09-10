import { redirect } from "next/navigation";

import { MembersManager } from "@/features/operations/members-manager";
import { requireTenantSession } from "@/lib/auth/session";

export default async function MembersPage() {
  const session = await requireTenantSession();
  if (!session.permissions.includes("members.view")) redirect("/dashboard");

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
      />
    </section>
  );
}

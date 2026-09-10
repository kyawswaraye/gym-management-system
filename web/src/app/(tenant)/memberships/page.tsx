import { redirect } from "next/navigation";

import { MembershipsManager } from "@/features/operations/memberships-manager";
import { requireTenantSession } from "@/lib/auth/session";

export default async function MembershipsPage() {
  const session = await requireTenantSession();
  if (!session.permissions.includes("memberships.view")) redirect("/dashboard");

  return (
    <section aria-labelledby="memberships-title">
      <h1 className="text-2xl font-bold" id="memberships-title">
        Memberships
      </h1>
      <p className="muted mb-6 mt-2">
        Create plans, start memberships, and track expiry and balances.
      </p>
      <MembershipsManager
        canCancel={session.permissions.includes("memberships.cancel")}
        canCreate={session.permissions.includes("memberships.create")}
        canUpdate={session.permissions.includes("memberships.update")}
        currency={session.gym?.currency ?? "THB"}
      />
    </section>
  );
}

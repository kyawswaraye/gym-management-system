import { redirect } from "next/navigation";

import { OwnersManager } from "@/features/operations/owners-manager";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function OwnersPage() {
  const session = await requireTenantSession();
  if (!session.gym_membership?.is_owner) redirect("/dashboard");

  return (
    <section aria-labelledby="owners-title">
      <h1 id="owners-title" className="text-3xl font-bold">
        Owner management
      </h1>
      <p className="muted mt-2">
        Invite equal-authority Owners and protect the gym from becoming
        ownerless.
      </p>
      <div className="mt-6">
        <OwnersManager
          currentMembershipId={session.gym_membership.id}
          mfaRecent={session.mfa_recent}
        />
      </div>
    </section>
  );
}

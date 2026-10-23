import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { OwnersManager } from "@/features/operations/owners-manager";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function OwnersPage() {
  const session = await requireTenantSession();
  if (!session.gym_membership?.is_owner) redirect("/dashboard");

  return (
    <section aria-labelledby="owners-title">
      <PageHeader
        description="Invite equal-authority Owners and protect the gym from becoming ownerless."
        eyebrow="Operations"
        title="Owner management"
        titleId="owners-title"
      />
      <OwnersManager
        currentMembershipId={session.gym_membership.id}
        mfaRecent={session.mfa_recent}
      />
    </section>
  );
}

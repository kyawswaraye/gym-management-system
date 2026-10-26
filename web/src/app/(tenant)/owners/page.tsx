import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { OwnersManager } from "@/features/operations/owners-manager";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Owners" };

export default async function OwnersPage() {
  const session = await requireTenantSession();
  if (!session.gym_membership?.is_owner) redirect("/dashboard");

  return (
    <section aria-labelledby="owners-title">
      <PageHeader
        description="Invite another owner with the same authority. The gym always keeps at least one active owner."
        eyebrow="Operations"
        title="Owners"
        titleId="owners-title"
      />
      <OwnersManager
        currentMembershipId={session.gym_membership.id}
        mfaRecent={session.mfa_recent}
      />
    </section>
  );
}

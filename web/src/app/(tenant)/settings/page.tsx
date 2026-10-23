import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { GymSettingsManager } from "@/features/operations/gym-settings-manager";
import { gymSettingsAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function GymSettingsPage() {
  const session = await requireTenantSession();
  const access = gymSettingsAccess(session);
  if (!access.canViewProfile && !access.canViewSettings) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="gym-settings-title">
      <PageHeader
        description="Maintain this gym's profile and operational settings. Lifecycle and subscription changes stay with platform administration."
        eyebrow="Operations"
        title="Gym settings"
        titleId="gym-settings-title"
      />
      <GymSettingsManager {...access} />
    </section>
  );
}

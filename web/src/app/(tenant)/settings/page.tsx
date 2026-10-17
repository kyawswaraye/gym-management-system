import { redirect } from "next/navigation";

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
      <h1 id="gym-settings-title" className="text-3xl font-bold">
        Gym settings
      </h1>
      <p className="muted mt-2">
        Maintain this gym&apos;s profile and operational settings. Lifecycle and
        subscription changes stay with platform administration.
      </p>
      <div className="mt-6">
        <GymSettingsManager {...access} />
      </div>
    </section>
  );
}

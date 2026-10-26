import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { GymSettingsManager } from "@/features/operations/gym-settings-manager";
import { gymSettingsAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Gym settings" };

export default async function GymSettingsPage() {
  const session = await requireTenantSession();
  const access = gymSettingsAccess(session);
  if (!access.canViewProfile && !access.canViewSettings) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="gym-settings-title">
      <PageHeader
        description="Name, contact details, and the day-to-day rules for check-in, numbers, classes, and reminders."
        eyebrow="Operations"
        title="Gym settings"
        titleId="gym-settings-title"
      />
      <GymSettingsManager {...access} />
    </section>
  );
}

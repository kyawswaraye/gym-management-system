import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { TrainerDashboardManager } from "@/features/pt/trainer-dashboard";
import {
  canOpenTrainerDashboard,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export async function generateMetadata() {
  return pageMetadata("Trainer dashboard");
}

export default async function TrainerDashboardPage() {
  const session = await requireTenantSession();
  if (!canOpenTrainerDashboard(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="trainer-dashboard-title">
      <PageHeader
        description="Your assigned members and personal-training sessions. Counts include only your sessions."
        eyebrow="Personal training"
        title="Trainer dashboard"
        titleId="trainer-dashboard-title"
      />
      <TrainerDashboardManager />
    </section>
  );
}

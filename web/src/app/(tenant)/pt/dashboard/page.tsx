import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { TrainerDashboardManager } from "@/features/pt/trainer-dashboard";
import {
  canOpenTrainerDashboard,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Trainer dashboard" };

export default async function TrainerDashboardPage() {
  const session = await requireTenantSession();
  if (!canOpenTrainerDashboard(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="trainer-dashboard-title">
      <h1 className="text-3xl font-bold" id="trainer-dashboard-title">
        Trainer dashboard
      </h1>
      <p className="muted mb-6 mt-2">
        Your assigned members and personal-training sessions. Counts include
        only your sessions.
      </p>
      <TrainerDashboardManager />
    </section>
  );
}

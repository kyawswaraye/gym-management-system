import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PlatformDashboard } from "@/features/platform/platform-dashboard";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Dashboard" };
export const dynamic = "force-dynamic";

export default async function SuperAdminDashboardPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.dashboard.view")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-dashboard-title">
      <PageHeader
        description="Gym, subscription, and plan counts. Open a section from the sidebar to act on them."
        eyebrow="GymPilot MM"
        title="Dashboard"
        titleId="platform-dashboard-title"
      />
      <PlatformDashboard canView />
    </section>
  );
}

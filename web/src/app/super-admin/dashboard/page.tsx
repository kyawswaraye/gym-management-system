import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PlatformDashboard } from "@/features/platform/platform-dashboard";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Platform dashboard" };
export const dynamic = "force-dynamic";

export default async function SuperAdminDashboardPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.dashboard.view")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-dashboard-title">
      <h1 id="platform-dashboard-title" className="text-3xl font-bold">
        Platform dashboard
      </h1>
      <p className="muted mt-2">Gym, subscription, and plan status counts.</p>
      <div className="mt-6">
        <PlatformDashboard canView />
      </div>
    </section>
  );
}

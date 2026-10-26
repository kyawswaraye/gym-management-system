import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { SubscriptionsManager } from "@/features/platform/subscriptions-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Subscriptions" };
export const dynamic = "force-dynamic";

export default async function SuperAdminSubscriptionsPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.subscriptions.manage")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-subscriptions-title">
      <PageHeader
        description="Update a plan's name, price, billing interval, or status. Retired plans stay on gyms that already use them."
        eyebrow="GymPilot MM"
        title="Subscriptions"
        titleId="platform-subscriptions-title"
      />
      <SubscriptionsManager canManage />
    </section>
  );
}

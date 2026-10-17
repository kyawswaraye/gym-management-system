import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SubscriptionsManager } from "@/features/platform/subscriptions-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Subscription plans" };
export const dynamic = "force-dynamic";

export default async function SuperAdminSubscriptionsPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.subscriptions.manage")) {
    redirect(platformHomePath(session));
  }

  return (
    <section aria-labelledby="platform-subscriptions-title">
      <h1 id="platform-subscriptions-title" className="text-3xl font-bold">
        Subscription plans
      </h1>
      <p className="muted mt-2">
        Review and update plan name, price, billing interval, and status.
      </p>
      <div className="mt-6">
        <SubscriptionsManager canManage />
      </div>
    </section>
  );
}

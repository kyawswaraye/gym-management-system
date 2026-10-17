import { redirect } from "next/navigation";

import { GymDetailManager } from "@/features/platform/gym-detail-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function SuperAdminGymDetailPage({
  params,
}: {
  params: Promise<{ gymId: string }>;
}) {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.gyms.view")) {
    redirect(platformHomePath(session));
  }
  const { gymId } = await params;

  return (
    <section aria-labelledby="platform-gym-title">
      <h1 id="platform-gym-title" className="text-3xl font-bold">
        Gym administration
      </h1>
      <p className="muted mt-2">
        Review and manage this gym&apos;s profile, subscription and lifecycle.
      </p>
      <div className="mt-6">
        <GymDetailManager
          gymId={gymId}
          capabilities={{
            canUpdate: hasPermission(session, "platform.gyms.update"),
            canManageSubscriptions: hasPermission(
              session,
              "platform.subscriptions.manage",
            ),
            canActivate: hasPermission(session, "platform.gyms.activate"),
            canSuspend: hasPermission(session, "platform.gyms.suspend"),
            canViewAudit: hasPermission(session, "platform.audit.view"),
            canCreateOwner: hasPermission(session, "platform.owners.create"),
            mfaRecent: session.mfa_recent,
          }}
        />
      </div>
    </section>
  );
}

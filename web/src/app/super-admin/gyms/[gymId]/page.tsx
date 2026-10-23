import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Review and manage this gym's profile, subscription and lifecycle."
        eyebrow="Platform"
        title="Gym administration"
        titleId="platform-gym-title"
      />
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
    </section>
  );
}

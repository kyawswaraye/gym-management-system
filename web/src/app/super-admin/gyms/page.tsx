import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { GymsManager } from "@/features/platform/gyms-manager";
import { hasPermission, platformHomePath } from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function SuperAdminGymsPage() {
  const session = await requirePlatformSession();
  if (!hasPermission(session, "platform.gyms.view")) {
    redirect(platformHomePath(session));
  }
  const canProvision =
    hasPermission(session, "platform.gyms.create") &&
    hasPermission(session, "platform.owners.create");

  return (
    <section aria-labelledby="platform-gyms-title">
      <PageHeader
        description="Provision tenant gyms and review their platform lifecycle state."
        eyebrow="Platform"
        title="Gym administration"
        titleId="platform-gyms-title"
      />
      <GymsManager canProvision={canProvision} />
    </section>
  );
}

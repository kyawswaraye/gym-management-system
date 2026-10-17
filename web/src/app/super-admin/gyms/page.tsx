import { redirect } from "next/navigation";

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
      <h1 id="platform-gyms-title" className="text-3xl font-bold">
        Gym administration
      </h1>
      <p className="muted mt-2">
        Provision tenant gyms and review their platform lifecycle state.
      </p>
      <div className="mt-6">
        <GymsManager canProvision={canProvision} />
      </div>
    </section>
  );
}

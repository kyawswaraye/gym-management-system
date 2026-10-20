import { redirect } from "next/navigation";

import { TrainersManager } from "@/features/pt/trainers-manager";
import {
  canOpenPtTrainers,
  hasPermission,
  holdsProtectedManagement,
  holdsRole,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function TrainersPage() {
  const session = await requireTenantSession();
  if (!canOpenPtTrainers(session)) redirect(tenantHomePath(session));
  const canManage = hasPermission(session, "pt.assignments.manage");
  const restricted =
    holdsRole(session, "TRAINER") && !holdsProtectedManagement(session);

  return (
    <section aria-labelledby="trainers-title">
      <h1 className="text-2xl font-bold" id="trainers-title">
        Trainers
      </h1>
      <p className="muted mb-6 mt-2">
        Review trainers, current assignments, and the members assigned today.
      </p>
      <TrainersManager
        canClose={canManage}
        canCreate={canManage && !restricted}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

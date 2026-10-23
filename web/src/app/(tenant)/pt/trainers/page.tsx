import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Review your coaching team, current assignments, and members assigned today."
        eyebrow="Personal training"
        title="Trainers"
        titleId="trainers-title"
      />
      <TrainersManager
        canClose={canManage}
        canCreate={canManage && !restricted}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

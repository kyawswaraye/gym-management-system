import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { MemberPackagesManager } from "@/features/pt/member-packages-manager";
import {
  canAdjustMemberSessions,
  canOpenPtSchedule,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function MemberPackagesPage() {
  const session = await requireTenantSession();
  if (!canOpenPtSchedule(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="member-packages-title">
      <PageHeader
        description="Read grants, balances, and the session ledger. Grant and correction stay with a protected owner or manager who does not also hold trainer or reception."
        eyebrow="Personal training"
        title="Member packages"
        titleId="member-packages-title"
      />
      <MemberPackagesManager
        canAdjust={canAdjustMemberSessions(session)}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

import { redirect } from "next/navigation";

import { CommissionsManager } from "@/features/pt/commissions-manager";
import {
  canOpenPtCommissions,
  canPayPtCommission,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function CommissionsPage() {
  const session = await requireTenantSession();
  if (!canOpenPtCommissions(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="commissions-title">
      <h1 className="text-2xl font-bold" id="commissions-title">
        Commissions
      </h1>
      <p className="muted mb-6 mt-2">
        Review scoped commission balances and payouts. Recording a payout stays
        with a protected owner or manager who does not also hold trainer or
        reception, and who did not create the accrual.
      </p>
      <CommissionsManager
        canPay={canPayPtCommission(session)}
        currency={session.gym?.currency ?? ""}
        membershipId={session.gym_membership?.id ?? ""}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

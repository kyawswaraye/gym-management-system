import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { SalesManager } from "@/features/pt/sales-manager";
import {
  canApprovePtSale,
  canOpenPtSales,
  hasPermission,
  hasTenantCapability,
  maySelfCloseOwnFinance,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function PtSalesPage() {
  const session = await requireTenantSession();
  if (!canOpenPtSales(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="pt-sales-title">
      <PageHeader
        description="1) Create a draft with the member's money. 2) Submit. 3) Approve. Then pay the trainer from Commissions."
        eyebrow="Personal training"
        title="PT sales"
        titleId="pt-sales-title"
      />
      <SalesManager
        canApprove={canApprovePtSale(session)}
        canApproveOwn={maySelfCloseOwnFinance(session)}
        canCreate={hasPermission(session, "pt.sales.create")}
        canSubmit={hasPermission(session, "pt.sales.submit")}
        canUpdate={hasPermission(session, "pt.sales.update")}
        canViewReceipts={hasTenantCapability(
          session,
          "receipts.view",
          "finance",
        )}
        canVoid={hasPermission(session, "pt.sales.void")}
        currency={session.gym?.currency ?? ""}
        membershipId={session.gym_membership?.id ?? ""}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

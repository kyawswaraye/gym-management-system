import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { SalesManager } from "@/features/pt/sales-manager";
import {
  canApprovePtSale,
  canOpenPtSales,
  hasPermission,
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
        description="Draft a sale, optionally attach a package and payment, then submit it for a different owner or manager to approve."
        eyebrow="Personal training"
        title="PT sales"
        titleId="pt-sales-title"
      />
      <SalesManager
        canApprove={canApprovePtSale(session)}
        canCreate={hasPermission(session, "pt.sales.create")}
        canSubmit={hasPermission(session, "pt.sales.submit")}
        canUpdate={hasPermission(session, "pt.sales.update")}
        canVoid={hasPermission(session, "pt.sales.void")}
        currency={session.gym?.currency ?? ""}
        membershipId={session.gym_membership?.id ?? ""}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PaymentsManager } from "@/features/finance/payments-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function PaymentsPage() {
  const session = await requireTenantSession();
  const canViewPayments = hasTenantCapability(
    session,
    "payments.view",
    "finance",
  );
  const canViewReceipts = hasTenantCapability(
    session,
    "receipts.view",
    "finance",
  );
  if (!canViewPayments && !canViewReceipts) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="payments-title">
      <PageHeader
        description="Record membership and other payment drafts, then submit them for approval. Receipts stay with the posted payment."
        eyebrow="Finance"
        title="Payments"
        titleId="payments-title"
      />
      <PaymentsManager
        canApprove={session.permissions.includes("payments.approve")}
        canCreate={
          canViewPayments && session.permissions.includes("payments.create")
        }
        canRefund={session.permissions.includes("payments.refund")}
        canSubmit={session.permissions.includes("payments.submit")}
        canUpdate={session.permissions.includes("payments.update")}
        canViewReceipts={canViewReceipts}
        canVoid={session.permissions.includes("payments.void")}
        canVoidReceipt={session.permissions.includes("receipts.void")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

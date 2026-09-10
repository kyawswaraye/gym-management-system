import { redirect } from "next/navigation";

import { PaymentsManager } from "@/features/operations/payments-manager";
import { requireTenantSession } from "@/lib/auth/session";

export default async function PaymentsPage() {
  const session = await requireTenantSession();
  if (!session.permissions.includes("payments.view")) redirect("/dashboard");

  return (
    <section aria-labelledby="payments-title">
      <h1 className="text-2xl font-bold" id="payments-title">
        Payments
      </h1>
      <p className="muted mb-6 mt-2">
        Record, submit, and approve membership payments with immutable receipts.
      </p>
      <PaymentsManager
        canApprove={session.permissions.includes("payments.approve")}
        canCreate={session.permissions.includes("payments.create")}
        canRefund={session.permissions.includes("payments.refund")}
        canSubmit={session.permissions.includes("payments.submit")}
        canUpdate={session.permissions.includes("payments.update")}
        canVoid={session.permissions.includes("payments.void")}
        currency={session.gym?.currency ?? "THB"}
      />
    </section>
  );
}

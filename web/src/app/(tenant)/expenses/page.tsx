import { redirect } from "next/navigation";

import { ExpensesManager } from "@/features/finance/expenses-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function ExpensesPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "expenses.view", "finance")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="expenses-title">
      <h1 className="text-2xl font-bold" id="expenses-title">
        Expenses
      </h1>
      <p className="muted mb-6 mt-2">
        Keep custom categories and expense drafts inside this gym. Protected
        salary and commission categories stay unchanged.
      </p>
      <ExpensesManager
        canApprove={session.permissions.includes("expenses.approve")}
        canCreate={session.permissions.includes("expenses.create")}
        canManageCategories={session.permissions.includes(
          "expense_categories.manage",
        )}
        canSubmit={session.permissions.includes("expenses.submit")}
        canUpdate={session.permissions.includes("expenses.update")}
        canUploadReceipt={
          session.permissions.includes("files.upload") &&
          (session.permissions.includes("expenses.create") ||
            session.permissions.includes("expenses.update"))
        }
        canVoid={session.permissions.includes("expenses.void")}
        currency={session.gym?.currency ?? "THB"}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
        gymId={session.gym?.id ?? ""}
      />
    </section>
  );
}

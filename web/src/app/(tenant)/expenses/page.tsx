import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
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
      <PageHeader
        description="Save a draft and submit it for approval. Custom categories stay on their own tab, separate from salary and commission."
        eyebrow="Finance"
        title="Expenses"
        titleId="expenses-title"
      />
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

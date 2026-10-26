import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { InventoryManager } from "@/features/finance/inventory-manager";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function InventoryPage() {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "inventory.view", "finance")) {
    redirect(tenantHomePath(session));
  }

  return (
    <section aria-labelledby="inventory-title">
      <PageHeader
        description="Browse the catalog, restock from a product, and keep categories on their own tab. On-hand quantity and sales totals come from the server."
        eyebrow="Finance"
        title="Inventory"
        titleId="inventory-title"
      />
      <InventoryManager
        canAdjust={session.permissions.includes("inventory.adjust")}
        canManage={session.permissions.includes("inventory.manage")}
        defaultDate={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}

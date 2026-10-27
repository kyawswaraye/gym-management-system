import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { TestReceiptPrint } from "@/features/finance/receipt-print-screen";
import { receiptAutoPrintEnabled } from "@/features/finance/receipt-print";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  return pageMetadata("Test print");
}

export default async function ReceiptTestPrintPage({
  searchParams,
}: {
  searchParams: Promise<{ autoPrint?: string | string[] }>;
}) {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "receipts.view", "finance")) {
    redirect(tenantHomePath(session));
  }
  const query = await searchParams;
  return (
    <TestReceiptPrint
      autoPrint={receiptAutoPrintEnabled(query.autoPrint)}
      currency={session.gym?.currency ?? "MMK"}
      gymName={session.gym?.name ?? ""}
    />
  );
}

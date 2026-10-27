import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { ReceiptPrintFallback } from "@/features/finance/receipt-preview";
import { FinalReceiptPrint } from "@/features/finance/receipt-print-screen";
import {
  isReceiptId,
  receiptAutoPrintEnabled,
} from "@/features/finance/receipt-print";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  return pageMetadata("Receipt print");
}

export default async function ReceiptPrintPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ autoPrint?: string | string[] }>;
}) {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "receipts.view", "finance")) {
    redirect(tenantHomePath(session));
  }
  const { id } = await params;
  const query = await searchParams;
  const receiptId = id.trim().toLowerCase();
  if (!isReceiptId(receiptId)) {
    return <ReceiptPrintFallback message="Receipt was not found." />;
  }
  return (
    <FinalReceiptPrint
      autoPrint={receiptAutoPrintEnabled(query.autoPrint)}
      key={receiptId}
      receiptId={receiptId}
      timeZone={session.gym?.timezone ?? "Asia/Yangon"}
    />
  );
}

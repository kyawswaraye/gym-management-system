import type { ReactNode } from "react";

import { SessionKeepalive } from "@/features/auth/session-keepalive";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function ReceiptPrintLayout({
  children,
}: {
  children: ReactNode;
}) {
  await requireTenantSession();
  return (
    <main className="receipt-print-workspace grid min-h-screen justify-items-center bg-[#f0f2f5] px-4 py-6">
      <SessionKeepalive />
      {children}
    </main>
  );
}

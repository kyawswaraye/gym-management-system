import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function TenantLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await requireTenantSession();
  return <AppShell session={session}>{children}</AppShell>;
}

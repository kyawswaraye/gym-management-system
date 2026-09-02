import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { requirePlatformSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function SuperAdminLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await requirePlatformSession();
  return (
    <AppShell session={session} platform>
      {children}
    </AppShell>
  );
}

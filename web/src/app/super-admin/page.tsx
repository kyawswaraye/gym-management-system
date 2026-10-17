import type { Metadata } from "next";
import { redirect } from "next/navigation";

import {
  PLATFORM_ACCESS_PATH,
  platformHomePath,
} from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Platform access" };
export const dynamic = "force-dynamic";

export default async function PlatformAccessPage() {
  const session = await requirePlatformSession();
  const home = platformHomePath(session);
  if (home !== PLATFORM_ACCESS_PATH) redirect(home);

  return (
    <section aria-labelledby="platform-access-title">
      <h1 id="platform-access-title" className="text-3xl font-bold">
        No platform access
      </h1>
      <p className="muted mt-2">This account has no platform tool assigned.</p>
    </section>
  );
}

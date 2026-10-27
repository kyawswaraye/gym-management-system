import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import {
  PLATFORM_ACCESS_PATH,
  platformHomePath,
} from "@/lib/auth/authorization";
import { requirePlatformSession } from "@/lib/auth/session";

export async function generateMetadata() {
  return pageMetadata("Platform access");
}
export const dynamic = "force-dynamic";

export default async function PlatformAccessPage() {
  const session = await requirePlatformSession();
  const home = platformHomePath(session);
  if (home !== PLATFORM_ACCESS_PATH) redirect(home);

  return (
    <section aria-labelledby="platform-access-title">
      <PageHeader
        description="This account has no platform tool assigned."
        eyebrow="Platform"
        title="No platform access"
        titleId="platform-access-title"
      />
    </section>
  );
}

import { redirect } from "next/navigation";

import { AssistantChat } from "@/features/assistant/chat";
import { pageMetadata } from "@/i18n/metadata";
import { tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export async function generateMetadata() {
  return pageMetadata("Assistant");
}

export default async function AssistantPage() {
  const session = await requireTenantSession();
  const membershipId = session.gym_membership?.id;
  if (!membershipId) redirect(tenantHomePath(session));
  return <AssistantChat membershipId={membershipId} />;
}

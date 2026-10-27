import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { MemberDetail } from "@/features/operations/member-detail";
import {
  canOpenCheckIns,
  hasTenantCapability,
  memberPhotoAccess,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export default async function MemberDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireTenantSession();
  if (!hasTenantCapability(session, "members.view", "members")) {
    redirect(tenantHomePath(session));
  }
  const { id } = await params;
  const photo = memberPhotoAccess(session);
  return (
    <section aria-labelledby="member-title">
      <PageHeader
        eyebrow="Member directory"
        title="Member profile"
        titleId="member-title"
      />
      <MemberDetail
        canArchive={session.permissions.includes("members.archive")}
        canRestore={session.permissions.includes("members.restore")}
        canUpdate={session.permissions.includes("members.update")}
        canViewCheckIns={canOpenCheckIns(session)}
        canViewPayments={hasTenantCapability(
          session,
          "payments.view",
          "finance",
        )}
        canViewReceipts={hasTenantCapability(
          session,
          "receipts.view",
          "finance",
        )}
        canViewPhoto={photo.canView}
        memberId={id}
        timeZone={session.gym?.timezone ?? "Asia/Yangon"}
      />
    </section>
  );
}

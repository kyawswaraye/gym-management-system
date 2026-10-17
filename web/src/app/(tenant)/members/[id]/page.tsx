import { redirect } from "next/navigation";

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
      <h1 className="mb-6 text-2xl font-bold" id="member-title">
        Member profile
      </h1>
      <MemberDetail
        canArchive={session.permissions.includes("members.archive")}
        canDeletePhoto={photo.canDelete}
        canUpdate={session.permissions.includes("members.update")}
        canUploadPhoto={photo.canUpload}
        canViewCheckIns={canOpenCheckIns(session)}
        canViewPayments={hasTenantCapability(
          session,
          "payments.view",
          "finance",
        )}
        canViewPhoto={photo.canView}
        memberId={id}
        timeZone={session.gym?.timezone ?? "Asia/Yangon"}
      />
    </section>
  );
}

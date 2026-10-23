import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { CheckInsWorkspace } from "@/features/operations/check-ins-workspace";
import {
  checkInViewValue,
  checkInWorkspaceMode,
} from "@/features/operations/check-ins-mode";
import {
  canIssueCheckInChallenge,
  canOpenCheckIns,
  canScanTrainerCheckIn,
  hasPermission,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

function checkInSummary(canDesk: boolean, canQr: boolean): string {
  if (canDesk && canQr) {
    return "Record a visit at the desk, or show a rotating QR code for members to scan.";
  }
  if (canQr) {
    return "Show this rotating code at the desk. Members cannot scan it from a staff login.";
  }
  return "Search a member, record a manual visit, and check that visit out.";
}

export default async function CheckInsPage({
  searchParams,
}: {
  searchParams?: Promise<{ view?: string | string[] }>;
} = {}) {
  const session = await requireTenantSession();
  const canDesk = canOpenCheckIns(session);
  const canQr = canIssueCheckInChallenge(session);
  if (!canDesk && !canQr) {
    redirect(tenantHomePath(session));
  }
  const view = checkInViewValue(
    searchParams ? (await searchParams).view : undefined,
  );
  const mode = checkInWorkspaceMode({ canDesk, canQr, view });
  if (!mode) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="check-ins-title">
      <PageHeader
        description={checkInSummary(canDesk, canQr)}
        eyebrow="Front desk"
        title="Check-ins"
        titleId="check-ins-title"
      />
      <CheckInsWorkspace
        canCreate={hasPermission(session, "checkins.create")}
        canDesk={canDesk}
        canQr={canQr}
        canScan={canScanTrainerCheckIn(session)}
        canUpdate={hasPermission(session, "checkins.update")}
        membershipId={session.gym_membership?.id ?? ""}
        mode={mode}
        timeZone={session.gym?.timezone ?? "Asia/Yangon"}
      />
    </section>
  );
}

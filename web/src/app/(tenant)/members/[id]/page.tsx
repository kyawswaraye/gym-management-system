import { redirect } from "next/navigation";

import { MemberDetail } from "@/features/operations/member-detail";
import { requireTenantSession } from "@/lib/auth/session";

export default async function MemberDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireTenantSession();
  if (!session.permissions.includes("members.view")) redirect("/dashboard");
  const { id } = await params;
  return (
    <section aria-labelledby="member-title">
      <h1 className="mb-6 text-2xl font-bold" id="member-title">
        Member profile
      </h1>
      <MemberDetail
        canUpdate={session.permissions.includes("members.update")}
        memberId={id}
      />
    </section>
  );
}

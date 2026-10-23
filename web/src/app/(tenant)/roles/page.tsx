import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { RoleManager } from "@/features/operations/role-manager";
import { roleAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Roles" };

export default async function RolesPage() {
  const session = await requireTenantSession();
  const access = roleAccess(session);
  if (!access.canView) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="roles-title">
      <PageHeader
        description="Review protected and custom roles for this gym. Permission changes stay inside the delegation policy, and the server confirms every save."
        eyebrow="Operations"
        title="Roles"
        titleId="roles-title"
      />
      <RoleManager
        canManage={access.canManage}
        isOwner={access.isOwner}
        permissions={access.permissions}
      />
    </section>
  );
}

import { pageMetadata } from "@/i18n/metadata";
import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { RoleManager } from "@/features/operations/role-manager";
import { roleAccess, tenantHomePath } from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  return pageMetadata("Roles");
}

export default async function RolesPage() {
  const session = await requireTenantSession();
  const access = roleAccess(session);
  if (!access.canView) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="roles-title">
      <PageHeader
        description="See what each role can do. Custom roles can be renamed or archived, and every permission change is checked again when you save."
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

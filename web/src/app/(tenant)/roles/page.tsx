import type { Metadata } from "next";
import { redirect } from "next/navigation";

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
      <h1 id="roles-title" className="text-3xl font-bold">
        Roles
      </h1>
      <p className="muted mt-2">
        Review protected and custom roles for this gym. Permission changes stay
        inside the delegation policy, and the server confirms every save.
      </p>
      <div className="mt-6">
        <RoleManager
          canManage={access.canManage}
          isOwner={access.isOwner}
          permissions={access.permissions}
        />
      </div>
    </section>
  );
}

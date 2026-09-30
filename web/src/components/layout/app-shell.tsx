import Link from "next/link";
import type { ReactNode } from "react";

import { LogoutButton } from "@/features/auth/logout-button";
import { hasTenantCapability, tenantHomePath } from "@/lib/auth/authorization";
import type { CurrentUser } from "@/types/api";

export function AppShell({
  session,
  children,
  platform = false,
}: {
  session: CurrentUser;
  children: ReactNode;
  platform?: boolean;
}) {
  const canUsePlatform = session.permissions.some((code) =>
    code.startsWith("platform."),
  );
  const gymName = session.gym?.name ?? "Selected gym";
  const homePath = platform
    ? "/super-admin/dashboard"
    : tenantHomePath(session);
  const canViewDashboard = hasTenantCapability(
    session,
    "reports.view",
    "reports",
  );
  const canViewMembers = hasTenantCapability(
    session,
    "members.view",
    "members",
  );
  const canViewMemberships = hasTenantCapability(
    session,
    "memberships.view",
    "memberships",
  );
  const canViewPayments = hasTenantCapability(
    session,
    "payments.view",
    "finance",
  );

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b border-slate-200 bg-slate-950 p-5 text-white md:min-h-screen md:border-b-0">
        <Link className="text-xl font-bold" href={homePath}>
          Gym Management
        </Link>
        <p className="mt-2 text-sm text-slate-300">
          {platform ? "Platform workspace" : gymName}
        </p>
        <nav className="mt-6 flex flex-wrap gap-2 md:grid" aria-label="Primary">
          {(platform || canViewDashboard) && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href={platform ? "/super-admin/dashboard" : "/dashboard"}
            >
              Dashboard
            </Link>
          )}
          {!platform && canViewMembers && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/members"
            >
              Members
            </Link>
          )}
          {!platform && canViewMemberships && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/memberships"
            >
              Memberships
            </Link>
          )}
          {!platform && canViewPayments && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/payments"
            >
              Payments
            </Link>
          )}
          {!platform && canUsePlatform && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/super-admin/dashboard"
            >
              Super admin
            </Link>
          )}
          {platform && session.gym && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href={tenantHomePath(session)}
            >
              Tenant workspace
            </Link>
          )}
        </nav>
      </aside>
      <div>
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4">
          <p className="min-w-0 truncate">
            <span className="font-semibold">
              {session.user.first_name || session.user.email}
            </span>
            <span className="muted ml-2 text-sm">{session.user.email}</span>
          </p>
          <LogoutButton />
        </header>
        <main className="p-5 md:p-8">{children}</main>
      </div>
    </div>
  );
}

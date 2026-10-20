import Link from "next/link";
import type { ReactNode } from "react";

import { LogoutButton } from "@/features/auth/logout-button";
import {
  canIssueCheckInChallenge,
  canOpenCheckIns,
  canOpenClasses,
  canOpenGymSettings,
  canOpenPtCommissions,
  canOpenPtSales,
  canOpenPtSchedule,
  canOpenPtTrainers,
  canOpenTrainerDashboard,
  hasPermission,
  hasTenantCapability,
  platformHomePath,
  tenantHomePath,
  trainerAssignmentRestricted,
} from "@/lib/auth/authorization";
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
    ? platformHomePath(session)
    : tenantHomePath(session);
  const canViewPlatformDashboard = hasPermission(
    session,
    "platform.dashboard.view",
  );
  const canManageSubscriptions = hasPermission(
    session,
    "platform.subscriptions.manage",
  );
  const canViewPlatformAudit = hasPermission(session, "platform.audit.view");
  const canViewDashboard =
    hasTenantCapability(session, "reports.view", "reports") &&
    !trainerAssignmentRestricted(session);
  const canViewTrainerDashboard = canOpenTrainerDashboard(session);
  const canViewReports =
    hasTenantCapability(session, "reports.view", "reports") &&
    !trainerAssignmentRestricted(session);
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
  const canViewPayments =
    hasTenantCapability(session, "payments.view", "finance") ||
    hasTenantCapability(session, "receipts.view", "finance");
  const canViewCashBook = hasTenantCapability(
    session,
    "cashbook.view",
    "finance",
  );
  const canViewExpenses = hasTenantCapability(
    session,
    "expenses.view",
    "finance",
  );
  const canViewSalaries = hasTenantCapability(
    session,
    "salaries.view",
    "finance",
  );
  const canViewInventory = hasTenantCapability(
    session,
    "inventory.view",
    "finance",
  );
  const canViewRetail = hasTenantCapability(session, "retail.view", "finance");
  const canViewCheckIns = canOpenCheckIns(session);
  const canIssueChallenge = canIssueCheckInChallenge(session);
  const canViewPtTrainers = canOpenPtTrainers(session);
  const canViewPtSchedule = canOpenPtSchedule(session);
  const canViewPtSales = canOpenPtSales(session);
  const canViewPtCommissions = canOpenPtCommissions(session);
  const canViewClasses = canOpenClasses(session);
  const canViewStaff = hasPermission(session, "staff.view");
  const canViewRoles = hasPermission(session, "roles.view");
  const canViewTenantAudit = hasPermission(session, "audit.view");

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
          {!platform && canViewDashboard && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/dashboard"
            >
              Dashboard
            </Link>
          )}
          {!platform && canViewTrainerDashboard && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/dashboard"
            >
              Trainer dashboard
            </Link>
          )}
          {!platform && canViewReports && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/reports"
            >
              Reports
            </Link>
          )}
          {platform && canViewPlatformDashboard && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/super-admin/dashboard"
            >
              Dashboard
            </Link>
          )}
          {platform && hasPermission(session, "platform.gyms.view") && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/super-admin/gyms"
            >
              Gyms
            </Link>
          )}
          {platform && canManageSubscriptions && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/super-admin/subscriptions"
            >
              Subscriptions
            </Link>
          )}
          {platform && canViewPlatformAudit && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/super-admin/audit-logs"
            >
              Audit logs
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
          {!platform && canViewCheckIns && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/check-ins"
            >
              Check-ins
            </Link>
          )}
          {!platform && canIssueChallenge && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/check-ins/display"
            >
              Check-in QR
            </Link>
          )}
          {!platform && canViewPtTrainers && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/trainers"
            >
              Trainers
            </Link>
          )}
          {!platform && canViewPtSchedule && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/packages"
            >
              Packages
            </Link>
          )}
          {!platform && canViewPtSchedule && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/member-packages"
            >
              Member packages
            </Link>
          )}
          {!platform && canViewPtSales && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/sales"
            >
              PT sales
            </Link>
          )}
          {!platform && canViewPtCommissions && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/commissions"
            >
              Commissions
            </Link>
          )}
          {!platform && canViewPtSchedule && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/pt/bookings"
            >
              PT bookings
            </Link>
          )}
          {!platform && canViewClasses && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/classes"
            >
              Classes
            </Link>
          )}
          {!platform && canViewPayments && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/payments"
            >
              {hasTenantCapability(session, "payments.view", "finance")
                ? "Payments"
                : "Receipts"}
            </Link>
          )}
          {!platform && canViewCashBook && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/cash-book"
            >
              Cash book
            </Link>
          )}
          {!platform && canViewExpenses && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/expenses"
            >
              Expenses
            </Link>
          )}
          {!platform && canViewSalaries && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/salaries"
            >
              Salaries
            </Link>
          )}
          {!platform && canViewInventory && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/inventory"
            >
              Inventory
            </Link>
          )}
          {!platform && canViewRetail && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/retail-sales"
            >
              Retail sales
            </Link>
          )}
          {!platform && canViewStaff && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/staff"
            >
              Staff
            </Link>
          )}
          {!platform && canViewRoles && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/roles"
            >
              Roles
            </Link>
          )}
          {!platform && canOpenGymSettings(session) && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/settings"
            >
              Settings
            </Link>
          )}
          {!platform && canViewTenantAudit && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/audit-logs"
            >
              Audit logs
            </Link>
          )}
          {!platform && session.gym_membership?.is_owner && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/owners"
            >
              Owners
            </Link>
          )}
          {!session.mfa_enabled && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href="/mfa-setup"
            >
              Set up MFA
            </Link>
          )}
          {!platform && canUsePlatform && (
            <Link
              className="rounded-md px-3 py-2 hover:bg-slate-800"
              href={platformHomePath(session)}
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

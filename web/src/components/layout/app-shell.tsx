import Link from "next/link";
import type { ReactNode } from "react";

import { NavLink } from "@/components/layout/nav-link";

import { LogoutButton } from "@/features/auth/logout-button";
import { SessionKeepalive } from "@/features/auth/session-keepalive";
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
  const navigation: Array<{ href: string; label: string }> = [];
  if (!platform && canViewDashboard) {
    navigation.push({ href: "/dashboard", label: "Dashboard" });
  }
  if (!platform && canViewTrainerDashboard) {
    navigation.push({ href: "/pt/dashboard", label: "Trainer dashboard" });
  }
  if (!platform && canViewReports) {
    navigation.push({ href: "/reports", label: "Reports" });
  }
  if (platform && canViewPlatformDashboard) {
    navigation.push({ href: "/super-admin/dashboard", label: "Dashboard" });
  }
  if (platform && hasPermission(session, "platform.gyms.view")) {
    navigation.push({ href: "/super-admin/gyms", label: "Gyms" });
  }
  if (platform && canManageSubscriptions) {
    navigation.push({
      href: "/super-admin/subscriptions",
      label: "Subscriptions",
    });
  }
  if (platform && canViewPlatformAudit) {
    navigation.push({ href: "/super-admin/audit-logs", label: "Audit logs" });
  }
  if (!platform && canViewMembers) {
    navigation.push({ href: "/members", label: "Members" });
  }
  if (!platform && canViewMemberships) {
    navigation.push({ href: "/memberships", label: "Memberships" });
  }
  if (!platform && (canViewCheckIns || canIssueChallenge)) {
    navigation.push({ href: "/check-ins", label: "Check-ins" });
  }
  if (!platform && canViewPtTrainers) {
    navigation.push({ href: "/pt/trainers", label: "Trainers" });
  }
  if (!platform && canViewPtSchedule) {
    navigation.push({ href: "/pt/packages", label: "Packages" });
  }
  if (!platform && canViewPtSchedule) {
    navigation.push({ href: "/pt/member-packages", label: "Member packages" });
  }
  if (!platform && canViewPtSales) {
    navigation.push({ href: "/pt/sales", label: "PT sales" });
  }
  if (!platform && canViewPtCommissions) {
    navigation.push({ href: "/pt/commissions", label: "Commissions" });
  }
  if (!platform && canViewPtSchedule) {
    navigation.push({ href: "/pt/bookings", label: "PT bookings" });
  }
  if (!platform && canViewClasses) {
    navigation.push({ href: "/classes", label: "Classes" });
  }
  if (!platform && canViewPayments) {
    navigation.push({
      href: "/payments",
      label: hasTenantCapability(session, "payments.view", "finance")
        ? "Payments"
        : "Receipts",
    });
  }
  if (!platform && canViewCashBook) {
    navigation.push({ href: "/cash-book", label: "Cash book" });
  }
  if (!platform && canViewExpenses) {
    navigation.push({ href: "/expenses", label: "Expenses" });
  }
  if (!platform && canViewSalaries) {
    navigation.push({ href: "/salaries", label: "Salaries" });
  }
  if (!platform && canViewInventory) {
    navigation.push({ href: "/inventory", label: "Inventory" });
  }
  if (!platform && canViewRetail) {
    navigation.push({ href: "/retail-sales", label: "Retail sales" });
  }
  if (!platform && canViewStaff) {
    navigation.push({ href: "/staff", label: "Staff" });
  }
  if (!platform && canViewRoles) {
    navigation.push({ href: "/roles", label: "Roles" });
  }
  if (!platform && canOpenGymSettings(session)) {
    navigation.push({ href: "/settings", label: "Settings" });
  }
  if (!platform && canViewTenantAudit) {
    navigation.push({ href: "/audit-logs", label: "Audit logs" });
  }
  if (!platform && session.gym_membership?.is_owner) {
    navigation.push({ href: "/owners", label: "Owners" });
  }
  if (!session.mfa_enabled) {
    navigation.push({ href: "/mfa-setup", label: "Set up MFA" });
  }
  if (!platform && canUsePlatform) {
    navigation.push({
      href: platformHomePath(session),
      label: "Super admin",
    });
  }
  if (platform && session.gym) {
    navigation.push({
      href: tenantHomePath(session),
      label: "Tenant workspace",
    });
  }
  const navigationHrefs = navigation.map((item) => item.href);

  return (
    <div className="min-h-screen bg-[#f6f7f9]">
      <SessionKeepalive />
      <aside className="border-b border-slate-700 bg-gradient-to-b from-[#25324a] via-[#2d3b55] to-[#34445f] p-4 text-white shadow-xl shadow-slate-950/15 md:fixed md:inset-y-0 md:left-0 md:z-30 md:w-64 md:overflow-y-auto md:border-r md:border-b-0 md:p-6">
        <Link
          aria-label="Gym Management"
          className="flex items-center gap-3"
          href={homePath}
        >
          <span
            className="grid size-10 shrink-0 place-items-center rounded-xl bg-amber-400 text-lg font-black text-slate-900 shadow-lg shadow-slate-950/20"
            aria-hidden="true"
          >
            G
          </span>
          <span className="min-w-0">
            <span className="block text-lg leading-tight font-bold tracking-tight">
              Gym Management
            </span>
            <span className="mt-1 block truncate text-xs font-medium text-slate-300">
              {platform ? "Platform workspace" : gymName}
            </span>
          </span>
        </Link>
        <p className="mt-7 hidden px-3 text-[0.65rem] font-bold tracking-[0.18em] text-amber-300/70 uppercase md:block">
          Workspace
        </p>
        <nav
          aria-label="Primary"
          className="mt-4 flex gap-2 overflow-x-auto pb-1 md:grid md:gap-1 md:overflow-visible md:pb-6"
        >
          {navigation.map((item) => (
            <NavLink
              href={item.href}
              key={`${item.label}:${item.href}`}
              peers={navigationHrefs}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 md:ml-64">
        <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 bg-white/90 px-5 py-4 shadow-sm backdrop-blur-md">
          <p className="min-w-0 truncate">
            <span className="font-semibold">
              {session.user.first_name || session.user.email}
            </span>
            <span className="muted ml-2 text-sm">{session.user.email}</span>
          </p>
          <LogoutButton />
        </header>
        <main className="mx-auto w-full max-w-[100rem] p-5 md:p-8">
          {children}
        </main>
      </div>
    </div>
  );
}

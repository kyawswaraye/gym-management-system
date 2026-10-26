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

export type NavLinkItem = {
  href: string;
  label: string;
};

export type NavEntry =
  | ({ kind: "link" } & NavLinkItem)
  | { kind: "folder"; label: string; links: readonly NavLinkItem[] };

function add(entries: NavEntry[], label: string, links: NavLinkItem[]) {
  const [only] = links;
  if (!only) return;
  if (links.length === 1) {
    entries.push({ kind: "link", href: only.href, label: only.label });
    return;
  }
  entries.push({ kind: "folder", label, links });
}

export function navigationHrefs(entries: readonly NavEntry[]): string[] {
  return entries.flatMap((entry) =>
    entry.kind === "link" ? [entry.href] : entry.links.map((link) => link.href),
  );
}

export function workspaceNavigation(
  session: CurrentUser,
  platform = false,
): NavEntry[] {
  const entries: NavEntry[] = [];

  if (platform) {
    if (hasPermission(session, "platform.dashboard.view")) {
      add(entries, "Dashboard", [
        { href: "/super-admin/dashboard", label: "Dashboard" },
      ]);
    }
    if (hasPermission(session, "platform.gyms.view")) {
      add(entries, "Gyms", [{ href: "/super-admin/gyms", label: "Gyms" }]);
    }
    if (hasPermission(session, "platform.subscriptions.manage")) {
      add(entries, "Subscriptions", [
        { href: "/super-admin/subscriptions", label: "Subscriptions" },
      ]);
    }
    if (hasPermission(session, "platform.audit.view")) {
      add(entries, "Audit logs", [
        { href: "/super-admin/audit-logs", label: "Audit logs" },
      ]);
    }
    if (session.gym) {
      add(entries, "Tenant workspace", [
        { href: tenantHomePath(session), label: "Tenant workspace" },
      ]);
    }
    return entries;
  }

  const dashboard: NavLinkItem[] = [];
  if (
    hasTenantCapability(session, "reports.view", "reports") &&
    !trainerAssignmentRestricted(session)
  ) {
    dashboard.push({ href: "/dashboard", label: "Dashboard" });
  }
  if (canOpenTrainerDashboard(session)) {
    dashboard.push({ href: "/pt/dashboard", label: "Trainer dashboard" });
  }
  add(entries, "Dashboard", dashboard);

  const members: NavLinkItem[] = [];
  if (hasTenantCapability(session, "members.view", "members")) {
    members.push({ href: "/members", label: "Members" });
  }
  if (hasTenantCapability(session, "memberships.view", "memberships")) {
    members.push({ href: "/memberships", label: "Memberships" });
  }
  add(entries, "Members", members);

  if (canOpenCheckIns(session) || canIssueCheckInChallenge(session)) {
    add(entries, "Check-ins", [{ href: "/check-ins", label: "Check-ins" }]);
  }

  const training: NavLinkItem[] = [];
  if (canOpenPtTrainers(session)) {
    training.push({ href: "/pt/trainers", label: "Trainers" });
  }
  if (canOpenPtSchedule(session)) {
    training.push(
      { href: "/pt/packages", label: "Packages" },
      { href: "/pt/member-packages", label: "Member packages" },
    );
  }
  if (canOpenPtSales(session)) {
    training.push({ href: "/pt/sales", label: "PT sales" });
  }
  if (canOpenPtCommissions(session)) {
    training.push({ href: "/pt/commissions", label: "Commissions" });
  }
  if (canOpenPtSchedule(session)) {
    training.push({ href: "/pt/bookings", label: "PT bookings" });
  }
  if (canOpenClasses(session)) {
    training.push({ href: "/classes", label: "Classes" });
  }
  add(entries, "Training", training);

  const finance: NavLinkItem[] = [];
  if (
    hasTenantCapability(session, "payments.view", "finance") ||
    hasTenantCapability(session, "receipts.view", "finance")
  ) {
    finance.push({
      href: "/payments",
      label: hasTenantCapability(session, "payments.view", "finance")
        ? "Payments"
        : "Receipts",
    });
  }
  if (hasTenantCapability(session, "cashbook.view", "finance")) {
    finance.push({ href: "/cash-book", label: "Cash book" });
  }
  if (hasTenantCapability(session, "expenses.view", "finance")) {
    finance.push({ href: "/expenses", label: "Expenses" });
  }
  if (hasTenantCapability(session, "salaries.view", "finance")) {
    finance.push({ href: "/salaries", label: "Salaries" });
  }
  if (hasTenantCapability(session, "inventory.view", "finance")) {
    finance.push({ href: "/inventory", label: "Inventory" });
  }
  if (hasTenantCapability(session, "retail.view", "finance")) {
    finance.push({ href: "/retail-sales", label: "Retail sales" });
  }
  if (
    hasTenantCapability(session, "reports.view", "reports") &&
    !trainerAssignmentRestricted(session)
  ) {
    finance.push({ href: "/reports", label: "Reports" });
  }
  add(entries, "Finance", finance);

  const settings: NavLinkItem[] = [];
  if (canOpenGymSettings(session)) {
    settings.push({ href: "/settings", label: "Settings" });
  }
  if (hasPermission(session, "staff.view")) {
    settings.push({ href: "/staff", label: "Staff" });
  }
  if (hasPermission(session, "roles.view")) {
    settings.push({ href: "/roles", label: "Roles" });
  }
  if (session.gym_membership?.is_owner) {
    settings.push({ href: "/owners", label: "Owners" });
  }
  if (hasPermission(session, "audit.view")) {
    settings.push({ href: "/audit-logs", label: "Audit logs" });
  }
  add(entries, "Settings", settings);

  if (!session.mfa_enabled) {
    add(entries, "Set up MFA", [{ href: "/mfa-setup", label: "Set up MFA" }]);
  }
  if (session.permissions.some((code) => code.startsWith("platform."))) {
    add(entries, "Super admin", [
      { href: platformHomePath(session), label: "Super admin" },
    ]);
  }

  return entries;
}

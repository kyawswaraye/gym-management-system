import type { CurrentUser } from "@/types/api";

export function hasPermission(session: CurrentUser, code: string): boolean {
  return session.permissions.includes(code);
}

export function hasFeature(session: CurrentUser, code: string): boolean {
  return session.features.some((feature) => feature.feature__code === code);
}

export function canIssueCheckInChallenge(session: CurrentUser): boolean {
  return (
    hasPermission(session, "checkins.challenge.issue") &&
    hasFeature(session, "members") &&
    hasFeature(session, "memberships")
  );
}

export function canOpenCheckIns(session: CurrentUser): boolean {
  return (
    hasPermission(session, "checkins.view") &&
    hasFeature(session, "members") &&
    hasFeature(session, "memberships")
  );
}

export function holdsRole(session: CurrentUser, key: string): boolean {
  return session.gym_membership?.roles?.includes(key) ?? false;
}

export function holdsProtectedManagement(session: CurrentUser): boolean {
  return holdsRole(session, "OWNER") || holdsRole(session, "MANAGER");
}

export function trainerAssignmentRestricted(session: CurrentUser): boolean {
  return holdsRole(session, "TRAINER") && !holdsProtectedManagement(session);
}

function protectedAdjuster(session: CurrentUser): boolean {
  return (
    holdsProtectedManagement(session) &&
    !holdsRole(session, "TRAINER") &&
    !holdsRole(session, "RECEPTIONIST")
  );
}

/** Owner, or one person who is both manager and receptionist. */
export function maySelfCloseOwnFinance(session: CurrentUser): boolean {
  if (session.gym_membership?.is_owner || holdsRole(session, "OWNER")) {
    return true;
  }
  return holdsRole(session, "MANAGER") && holdsRole(session, "RECEPTIONIST");
}

export function canOpenPtTrainers(session: CurrentUser): boolean {
  return hasTenantCapability(session, "pt.assignments.view", "pt");
}

export function canOpenPtSchedule(session: CurrentUser): boolean {
  return hasTenantCapability(session, "pt.schedule.view", "pt");
}

export function canOpenPtSales(session: CurrentUser): boolean {
  return hasTenantCapability(session, "pt.sales.view", "pt");
}

export function canOpenPtCommissions(session: CurrentUser): boolean {
  return hasTenantCapability(session, "pt.commissions.view", "pt");
}

export function canOpenClasses(session: CurrentUser): boolean {
  return hasTenantCapability(session, "classes.view", "pt");
}

export function canOpenTrainerDashboard(session: CurrentUser): boolean {
  return (
    holdsRole(session, "TRAINER") &&
    hasTenantCapability(session, "reports.view", "reports") &&
    hasFeature(session, "pt")
  );
}

export function canAdjustMemberSessions(session: CurrentUser): boolean {
  return (
    hasFeature(session, "pt") &&
    hasPermission(session, "pt.sessions.adjust") &&
    protectedAdjuster(session)
  );
}

export function canPayPtCommission(session: CurrentUser): boolean {
  return (
    hasFeature(session, "pt") &&
    hasPermission(session, "pt.commissions.pay") &&
    holdsProtectedManagement(session) &&
    !holdsRole(session, "TRAINER")
  );
}

export function canApprovePtSale(session: CurrentUser): boolean {
  return (
    hasFeature(session, "pt") &&
    hasPermission(session, "pt.sales.approve") &&
    holdsProtectedManagement(session) &&
    !holdsRole(session, "TRAINER")
  );
}

export function canOfferUnmeteredBooking(session: CurrentUser): boolean {
  return hasFeature(session, "pt") && protectedAdjuster(session);
}

export function canScanTrainerCheckIn(session: CurrentUser): boolean {
  return (
    holdsRole(session, "TRAINER") &&
    hasPermission(session, "checkins.create") &&
    hasFeature(session, "members") &&
    hasFeature(session, "memberships")
  );
}

export function hasTenantCapability(
  session: CurrentUser,
  permission: string,
  feature: string,
): boolean {
  return hasPermission(session, permission) && hasFeature(session, feature);
}

export function hasPlatformAccess(session: CurrentUser): boolean {
  return (
    session.principal_type === "PLATFORM" &&
    session.permissions.some((code) => code.startsWith("platform."))
  );
}

export type GymSettingsAccess = {
  canViewProfile: boolean;
  canUpdateProfile: boolean;
  canViewSettings: boolean;
  canManageSettings: boolean;
};

export function gymSettingsAccess(session: CurrentUser): GymSettingsAccess {
  const canViewProfile = hasPermission(session, "gym.view");
  const canViewSettings = hasPermission(session, "settings.view");
  return {
    canViewProfile,
    canUpdateProfile: canViewProfile && hasPermission(session, "gym.update"),
    canViewSettings,
    canManageSettings:
      canViewSettings &&
      Boolean(session.gym_membership?.is_owner) &&
      hasPermission(session, "settings.manage"),
  };
}

export function canOpenGymSettings(session: CurrentUser): boolean {
  const access = gymSettingsAccess(session);
  return access.canViewProfile || access.canViewSettings;
}

export type MemberPhotoAccess = {
  canUpload: boolean;
  canView: boolean;
  canDelete: boolean;
};

export function memberPhotoAccess(session: CurrentUser): MemberPhotoAccess {
  const canUpdateMember = hasPermission(session, "members.update");
  return {
    canUpload: canUpdateMember && hasPermission(session, "files.upload"),
    canView:
      hasPermission(session, "members.view") &&
      hasPermission(session, "files.view"),
    canDelete: canUpdateMember && hasPermission(session, "files.delete"),
  };
}

export type StaffAccess = {
  canView: boolean;
  canManage: boolean;
  canViewRoles: boolean;
  isOwner: boolean;
  permissions: readonly string[];
};

export function staffAccess(session: CurrentUser): StaffAccess {
  return {
    canView: hasPermission(session, "staff.view"),
    canManage: hasPermission(session, "staff.manage"),
    canViewRoles: hasPermission(session, "roles.view"),
    isOwner: Boolean(session.gym_membership?.is_owner),
    permissions: session.permissions,
  };
}

export type RoleAccess = {
  canView: boolean;
  canManage: boolean;
  isOwner: boolean;
  permissions: readonly string[];
};

export function roleAccess(session: CurrentUser): RoleAccess {
  return {
    canView: hasPermission(session, "roles.view"),
    canManage: hasPermission(session, "roles.manage"),
    isOwner: Boolean(session.gym_membership?.is_owner),
    permissions: session.permissions,
  };
}

export const PLATFORM_ACCESS_PATH = "/super-admin";

const PLATFORM_HOME_ROUTES = [
  ["platform.dashboard.view", "/super-admin/dashboard"],
  ["platform.gyms.view", "/super-admin/gyms"],
  ["platform.subscriptions.manage", "/super-admin/subscriptions"],
  ["platform.audit.view", "/super-admin/audit-logs"],
] as const;

export function platformHomePath(session: CurrentUser): string {
  for (const [permission, path] of PLATFORM_HOME_ROUTES) {
    if (hasPermission(session, permission)) return path;
  }
  return PLATFORM_ACCESS_PATH;
}

export function tenantHomePath(session: CurrentUser): string {
  if (
    trainerAssignmentRestricted(session) &&
    canOpenTrainerDashboard(session)
  ) {
    return "/pt/dashboard";
  }
  if (
    !trainerAssignmentRestricted(session) &&
    hasTenantCapability(session, "reports.view", "reports")
  ) {
    return "/dashboard";
  }
  if (hasTenantCapability(session, "members.view", "members")) {
    return "/members";
  }
  if (hasTenantCapability(session, "memberships.view", "memberships")) {
    return "/memberships";
  }
  if (
    hasTenantCapability(session, "payments.view", "finance") ||
    hasTenantCapability(session, "receipts.view", "finance")
  ) {
    return "/payments";
  }
  if (hasTenantCapability(session, "cashbook.view", "finance")) {
    return "/cash-book";
  }
  if (hasTenantCapability(session, "expenses.view", "finance")) {
    return "/expenses";
  }
  if (hasTenantCapability(session, "salaries.view", "finance")) {
    return "/salaries";
  }
  if (hasTenantCapability(session, "inventory.view", "finance")) {
    return "/inventory";
  }
  if (hasTenantCapability(session, "retail.view", "finance")) {
    return "/retail-sales";
  }
  if (canOpenPtTrainers(session)) return "/pt/trainers";
  if (canOpenPtSchedule(session)) return "/pt/packages";
  if (canOpenPtSales(session)) return "/pt/sales";
  if (canOpenPtCommissions(session)) return "/pt/commissions";
  if (canOpenClasses(session)) return "/classes";
  if (hasPermission(session, "staff.view")) return "/staff";
  if (hasPermission(session, "roles.view")) return "/roles";
  if (canOpenGymSettings(session)) return "/settings";
  if (hasPermission(session, "audit.view")) return "/audit-logs";
  return "/dashboard";
}

export function sessionHomePath(session: CurrentUser): string {
  if (hasPlatformAccess(session)) {
    return platformHomePath(session);
  }
  if (!session.gym || !session.gym_membership) {
    return "/select-gym";
  }
  return tenantHomePath(session);
}

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
  if (hasTenantCapability(session, "reports.view", "reports")) {
    return "/dashboard";
  }
  if (hasTenantCapability(session, "members.view", "members")) {
    return "/members";
  }
  if (hasTenantCapability(session, "memberships.view", "memberships")) {
    return "/memberships";
  }
  if (hasTenantCapability(session, "payments.view", "finance")) {
    return "/payments";
  }
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

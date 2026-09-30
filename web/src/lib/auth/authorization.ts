import type { CurrentUser } from "@/types/api";

export function hasPermission(session: CurrentUser, code: string): boolean {
  return session.permissions.includes(code);
}

export function hasFeature(session: CurrentUser, code: string): boolean {
  return session.features.some((feature) => feature.feature__code === code);
}

export function hasTenantCapability(
  session: CurrentUser,
  permission: string,
  feature: string,
): boolean {
  return hasPermission(session, permission) && hasFeature(session, feature);
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
  return "/dashboard";
}

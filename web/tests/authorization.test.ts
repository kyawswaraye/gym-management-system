import { describe, expect, it } from "vitest";

import {
  PLATFORM_ACCESS_PATH,
  canIssueCheckInChallenge,
  canOpenCheckIns,
  canOpenGymSettings,
  canOpenTrainerDashboard,
  gymSettingsAccess,
  hasFeature,
  hasTenantCapability,
  maySelfCloseOwnFinance,
  memberPhotoAccess,
  platformHomePath,
  sessionHomePath,
  roleAccess,
  staffAccess,
  tenantHomePath,
} from "@/lib/auth/authorization";
import type { CurrentUser } from "@/types/api";

function session(permissions: string[], features: string[]): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
  } as CurrentUser;
}

describe("tenant authorization helpers", () => {
  it("requires both permission and active feature", () => {
    const current = session(["members.view"], ["members"]);

    expect(hasFeature(current, "members")).toBe(true);
    expect(hasTenantCapability(current, "members.view", "members")).toBe(true);
    expect(hasTenantCapability(current, "members.view", "memberships")).toBe(
      false,
    );
    expect(hasTenantCapability(current, "payments.view", "members")).toBe(
      false,
    );
    expect(
      canOpenCheckIns(session(["checkins.view"], ["members", "memberships"])),
    ).toBe(true);
    expect(canOpenCheckIns(session(["checkins.view"], ["members"]))).toBe(
      false,
    );
    expect(
      canOpenCheckIns(session(["members.view"], ["members", "memberships"])),
    ).toBe(false);
    expect(
      canIssueCheckInChallenge(
        session(["checkins.challenge.issue"], ["members", "memberships"]),
      ),
    ).toBe(true);
    expect(
      canIssueCheckInChallenge(
        session(["checkins.view"], ["members", "memberships"]),
      ),
    ).toBe(false);
  });

  it("selects the first usable tenant route", () => {
    expect(tenantHomePath(session(["reports.view"], ["reports"]))).toBe(
      "/dashboard",
    );
    expect(
      tenantHomePath(session(["reports.view", "members.view"], ["members"])),
    ).toBe("/members");
    expect(tenantHomePath(session(["payments.view"], ["finance"]))).toBe(
      "/payments",
    );
    expect(tenantHomePath(session(["cashbook.view"], ["finance"]))).toBe(
      "/cash-book",
    );
    expect(tenantHomePath(session(["expenses.view"], ["finance"]))).toBe(
      "/expenses",
    );
    expect(tenantHomePath(session(["salaries.view"], ["finance"]))).toBe(
      "/salaries",
    );
    expect(tenantHomePath(session(["inventory.view"], ["finance"]))).toBe(
      "/inventory",
    );
    expect(tenantHomePath(session(["retail.view"], ["finance"]))).toBe(
      "/retail-sales",
    );
    expect(tenantHomePath(session(["cashbook.view"], []))).toBe("/dashboard");
    expect(tenantHomePath(session(["staff.view"], []))).toBe("/staff");
    expect(tenantHomePath(session(["staff.view", "roles.view"], []))).toBe(
      "/staff",
    );
    expect(tenantHomePath(session(["roles.view"], []))).toBe("/roles");
    expect(tenantHomePath(session(["staff.manage"], []))).toBe("/dashboard");
    expect(tenantHomePath(session(["roles.manage"], []))).toBe("/dashboard");
    expect(tenantHomePath(session(["audit.view"], []))).toBe("/audit-logs");
    expect(tenantHomePath(session(["staff.view", "audit.view"], []))).toBe(
      "/staff",
    );
    expect(tenantHomePath(session(["roles.view", "audit.view"], []))).toBe(
      "/roles",
    );
    expect(tenantHomePath(session(["gym.view", "audit.view"], []))).toBe(
      "/settings",
    );
    expect(
      tenantHomePath(session(["reports.view", "audit.view"], ["reports"])),
    ).toBe("/dashboard");
    expect(tenantHomePath(session(["platform.audit.view"], []))).toBe(
      "/dashboard",
    );
  });

  it("separates staff viewing, staff management, and role visibility", () => {
    expect(staffAccess(session(["staff.view", "roles.view"], []))).toEqual({
      canView: true,
      canManage: false,
      canViewRoles: true,
      isOwner: false,
      permissions: ["staff.view", "roles.view"],
    });
    expect(staffAccess(session(["staff.view", "staff.manage"], []))).toEqual({
      canView: true,
      canManage: true,
      canViewRoles: false,
      isOwner: false,
      permissions: ["staff.view", "staff.manage"],
    });
    expect(staffAccess(session(["roles.manage"], []))).toEqual({
      canView: false,
      canManage: false,
      canViewRoles: false,
      isOwner: false,
      permissions: ["roles.manage"],
    });
    const owner = {
      ...session(["staff.view", "staff.manage", "roles.view"], []),
      gym_membership: { is_owner: true },
    } as CurrentUser;
    expect(staffAccess(owner)).toMatchObject({
      isOwner: true,
      permissions: ["staff.view", "staff.manage", "roles.view"],
    });
  });

  it("gates role management on view, manage, and owner membership", () => {
    expect(roleAccess(session(["roles.view"], []))).toEqual({
      canView: true,
      canManage: false,
      isOwner: false,
      permissions: ["roles.view"],
    });
    expect(roleAccess(session(["roles.manage"], []))).toMatchObject({
      canView: false,
      canManage: true,
      isOwner: false,
    });
    const owner = {
      ...session(["roles.view", "roles.manage", "members.view"], []),
      gym_membership: { is_owner: true },
    } as CurrentUser;
    expect(roleAccess(owner)).toMatchObject({
      canView: true,
      canManage: true,
      isOwner: true,
      permissions: ["roles.view", "roles.manage", "members.view"],
    });
  });

  it("derives profile photo actions from live member and file permissions", () => {
    expect(
      memberPhotoAccess(
        session(
          ["members.view", "members.update", "files.upload", "files.view"],
          ["members"],
        ),
      ),
    ).toEqual({ canUpload: true, canView: true, canDelete: false });
    expect(
      memberPhotoAccess(
        session(["files.upload", "files.delete", "files.view"], ["members"]),
      ),
    ).toEqual({ canUpload: false, canView: false, canDelete: false });
    expect(
      memberPhotoAccess(
        session(
          [
            "members.view",
            "members.update",
            "files.upload",
            "files.view",
            "files.delete",
          ],
          [],
        ),
      ),
    ).toEqual({ canUpload: true, canView: true, canDelete: true });
  });

  it("keeps an account with no capabilities on the safe empty dashboard", () => {
    expect(tenantHomePath(session(["members.view"], []))).toBe("/dashboard");
  });

  it("opens gym settings for readers and reserves edits for authorized owners", () => {
    expect(tenantHomePath(session(["gym.view"], []))).toBe("/settings");
    expect(tenantHomePath(session(["settings.view"], []))).toBe("/settings");
    expect(
      tenantHomePath(session(["reports.view", "gym.view"], ["reports"])),
    ).toBe("/dashboard");

    expect(
      gymSettingsAccess(session(["settings.view", "settings.manage"], [])),
    ).toEqual({
      canViewProfile: false,
      canUpdateProfile: false,
      canViewSettings: true,
      canManageSettings: false,
    });

    const owner = {
      ...session(
        ["gym.view", "gym.update", "settings.view", "settings.manage"],
        [],
      ),
      gym_membership: { is_owner: true },
    } as CurrentUser;
    expect(gymSettingsAccess(owner)).toMatchObject({
      canViewProfile: true,
      canUpdateProfile: true,
      canViewSettings: true,
      canManageSettings: true,
    });

    const updateWithoutView = {
      ...session(["gym.update", "settings.manage"], []),
      gym_membership: { is_owner: true },
    } as CurrentUser;
    expect(canOpenGymSettings(updateWithoutView)).toBe(false);
    expect(gymSettingsAccess(updateWithoutView).canManageSettings).toBe(false);
    expect(gymSettingsAccess(updateWithoutView).canUpdateProfile).toBe(false);
  });

  it("routes authenticated users to their authorized workspace", () => {
    const platform = {
      ...session(["platform.gyms.view"], []),
      principal_type: "PLATFORM",
    } as CurrentUser;
    expect(sessionHomePath(platform)).toBe("/super-admin/gyms");

    const tenantWithInjectedPlatformPermission = session(
      ["platform.gyms.view", "members.view"],
      ["members"],
    );
    expect(sessionHomePath(tenantWithInjectedPlatformPermission)).toBe(
      "/select-gym",
    );

    const needsGym = session(["members.view"], ["members"]);
    expect(sessionHomePath(needsGym)).toBe("/select-gym");

    const tenant = {
      ...session(["members.view"], ["members"]),
      gym: { id: "gym-1" },
      gym_membership: { id: "membership-1" },
    } as CurrentUser;
    expect(sessionHomePath(tenant)).toBe("/members");

    const auditOnly = {
      ...session(["audit.view"], []),
      gym: { id: "gym-1" },
      gym_membership: { id: "membership-1" },
    } as CurrentUser;
    expect(sessionHomePath(auditOnly)).toBe("/audit-logs");
  });

  it("chooses a platform home from live permissions without a dashboard loop", () => {
    const platform = (permissions: string[]) =>
      ({
        ...session(permissions, []),
        principal_type: "PLATFORM",
      }) as CurrentUser;

    expect(
      platformHomePath(
        platform(["platform.dashboard.view", "platform.gyms.view"]),
      ),
    ).toBe("/super-admin/dashboard");
    expect(platformHomePath(platform(["platform.gyms.view"]))).toBe(
      "/super-admin/gyms",
    );
    expect(platformHomePath(platform(["platform.subscriptions.manage"]))).toBe(
      "/super-admin/subscriptions",
    );
    expect(platformHomePath(platform(["platform.audit.view"]))).toBe(
      "/super-admin/audit-logs",
    );
    expect(
      sessionHomePath(
        platform(["platform.audit.view", "platform.subscriptions.manage"]),
      ),
    ).toBe("/super-admin/subscriptions");
    expect(platformHomePath(platform(["platform.settings.manage"]))).toBe(
      PLATFORM_ACCESS_PATH,
    );
    expect(
      gymSettingsAccess(platform(["platform.settings.manage"]))
        .canManageSettings,
    ).toBe(false);
    expect(canOpenGymSettings(platform(["platform.settings.manage"]))).toBe(
      false,
    );
    expect(platformHomePath(platform(["platform.gyms.create"]))).toBe(
      PLATFORM_ACCESS_PATH,
    );
    expect(sessionHomePath(platform(["platform.settings.manage"]))).toBe(
      "/super-admin",
    );
    expect(platformHomePath(platform(["platform.dashboard.view"]))).not.toBe(
      "/super-admin",
    );
    expect(platformHomePath(platform(["platform.gyms.update"]))).not.toBe(
      "/super-admin/dashboard",
    );
  });

  it("lets the owner, or a combined manager and receptionist, close their own finance", () => {
    const withRoles = (roles: string[], isOwner = false) =>
      ({
        ...session([], []),
        gym_membership: { is_owner: isOwner, roles },
      }) as CurrentUser;

    expect(maySelfCloseOwnFinance(withRoles(["OWNER"], true))).toBe(true);
    expect(maySelfCloseOwnFinance(withRoles(["OWNER"]))).toBe(true);
    expect(maySelfCloseOwnFinance(withRoles(["MANAGER", "RECEPTIONIST"]))).toBe(
      true,
    );
    expect(maySelfCloseOwnFinance(withRoles(["MANAGER"]))).toBe(false);
    expect(maySelfCloseOwnFinance(withRoles(["RECEPTIONIST"]))).toBe(false);
  });

  it("sends a restricted trainer to the trainer dashboard before the owner dashboard", () => {
    const trainer = (
      permissions: string[],
      features: string[],
      roles: string[],
    ) =>
      ({
        ...session(permissions, features),
        gym: { id: "gym-1" },
        gym_membership: {
          id: "membership-1",
          is_owner: roles.includes("OWNER"),
          roles,
        },
      }) as CurrentUser;

    const capable = trainer(
      ["reports.view", "members.view"],
      ["reports", "pt", "members"],
      ["TRAINER"],
    );
    expect(canOpenTrainerDashboard(capable)).toBe(true);
    expect(tenantHomePath(capable)).toBe("/pt/dashboard");

    const ownerTrainer = trainer(
      ["reports.view"],
      ["reports", "pt"],
      ["OWNER", "TRAINER"],
    );
    expect(canOpenTrainerDashboard(ownerTrainer)).toBe(true);
    expect(tenantHomePath(ownerTrainer)).toBe("/dashboard");

    const managerTrainer = trainer(
      ["reports.view"],
      ["reports", "pt"],
      ["MANAGER", "TRAINER"],
    );
    expect(tenantHomePath(managerTrainer)).toBe("/dashboard");

    const missingPt = trainer(
      ["reports.view", "members.view"],
      ["reports", "members"],
      ["TRAINER"],
    );
    expect(canOpenTrainerDashboard(missingPt)).toBe(false);
    expect(tenantHomePath(missingPt)).toBe("/members");

    const missingReportsFeature = trainer(
      ["reports.view"],
      ["pt"],
      ["TRAINER"],
    );
    expect(canOpenTrainerDashboard(missingReportsFeature)).toBe(false);
    expect(tenantHomePath(missingReportsFeature)).toBe("/dashboard");

    const receptionist = trainer(
      ["reports.view"],
      ["reports", "pt"],
      ["RECEPTIONIST"],
    );
    expect(canOpenTrainerDashboard(receptionist)).toBe(false);
    expect(tenantHomePath(receptionist)).toBe("/dashboard");

    expect(
      canOpenTrainerDashboard(
        trainer(["reports.financial"], ["reports", "pt"], ["TRAINER"]),
      ),
    ).toBe(false);
  });
});

import { describe, expect, it } from "vitest";

import {
  hasFeature,
  hasTenantCapability,
  sessionHomePath,
  tenantHomePath,
} from "@/lib/auth/authorization";
import type { CurrentUser } from "@/types/api";

function session(permissions: string[], features: string[]): CurrentUser {
  return {
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
  });

  it("keeps an account with no capabilities on the safe empty dashboard", () => {
    expect(tenantHomePath(session(["members.view"], []))).toBe("/dashboard");
  });

  it("routes authenticated users to their authorized workspace", () => {
    const platform = session(["platform.gyms.view"], []);
    expect(sessionHomePath(platform)).toBe("/super-admin/dashboard");

    const needsGym = session(["members.view"], ["members"]);
    expect(sessionHomePath(needsGym)).toBe("/select-gym");

    const tenant = {
      ...session(["members.view"], ["members"]),
      gym: { id: "gym-1" },
      gym_membership: { id: "membership-1" },
    } as CurrentUser;
    expect(sessionHomePath(tenant)).toBe("/members");
  });
});

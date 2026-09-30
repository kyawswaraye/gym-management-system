import { describe, expect, it } from "vitest";

import {
  hasFeature,
  hasTenantCapability,
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
});

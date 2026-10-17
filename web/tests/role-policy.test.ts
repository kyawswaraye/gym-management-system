import { describe, expect, it } from "vitest";

import {
  assignmentRequiresOwner,
  roleIsAssignable,
} from "@/features/operations/role-policy";
import type { Role } from "@/types/api";

const RESERVED = [
  "payments.approve",
  "expenses.approve",
  "retail.approve",
  "pt.sales.approve",
  "salaries.approve",
];

function role(overrides: Partial<Role> = {}): Role {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    key: "TRAINER",
    name: "Trainer",
    description: "",
    is_active: true,
    is_protected: true,
    is_template: false,
    source_template_id: null,
    source_template_version: null,
    permission_codes: ["members.view"],
    permission_count: 1,
    assignment_count: 0,
    created_by_membership_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const managerRole = role({
  id: "55555555-5555-4555-8555-555555555555",
  key: "MANAGER",
  name: "Manager",
  is_protected: true,
  permission_codes: ["members.view", "staff.manage", ...RESERVED],
});
const receptionist = role({
  id: "11111111-1111-4111-8111-111111111111",
  key: "RECEPTIONIST",
  name: "Receptionist",
  permission_codes: ["members.view"],
});
const custom = role({
  id: "44444444-4444-4444-8444-444444444444",
  key: "DESK",
  name: "Desk",
  is_protected: false,
  permission_codes: ["members.view"],
});
const reserved = role({
  id: "66666666-6666-4666-8666-666666666666",
  key: "APPROVER",
  name: "Approver",
  is_protected: false,
  permission_codes: ["payments.approve"],
});
const settings = role({
  id: "77777777-7777-4777-8777-777777777777",
  key: "CONFIG",
  name: "Config",
  is_protected: false,
  permission_codes: ["settings.manage"],
});
const platform = role({
  id: "88888888-8888-4888-8888-888888888888",
  key: "OPS",
  name: "Platform ops",
  is_protected: false,
  permission_codes: ["platform.gyms.view"],
});
const catalog = [
  managerRole,
  receptionist,
  custom,
  reserved,
  settings,
  platform,
];
const live = ["members.view", "staff.manage"];

describe("role assignment policy", () => {
  it("lets an owner assign protected manager and in-policy roles", () => {
    const input = { isOwner: true, livePermissions: [], roles: catalog };

    expect(roleIsAssignable(managerRole, input)).toBe(true);
    expect(roleIsAssignable(receptionist, input)).toBe(true);
    expect(roleIsAssignable(custom, input)).toBe(true);
    expect(roleIsAssignable(reserved, input)).toBe(false);
    expect(roleIsAssignable(settings, input)).toBe(false);
    expect(roleIsAssignable(platform, input)).toBe(false);
  });

  it("limits a manager to roles inside the live delegable set", () => {
    const input = { isOwner: false, livePermissions: live, roles: catalog };

    expect(roleIsAssignable(receptionist, input)).toBe(true);
    expect(roleIsAssignable(custom, input)).toBe(true);
    expect(roleIsAssignable(managerRole, input)).toBe(false);
    expect(roleIsAssignable(reserved, input)).toBe(false);
    expect(roleIsAssignable(settings, input)).toBe(false);
    expect(roleIsAssignable(platform, input)).toBe(false);
    expect(
      roleIsAssignable(
        role({
          id: "99999999-9999-4999-8999-999999999999",
          key: "COACH",
          is_protected: false,
          permission_codes: ["reports.view"],
        }),
        input,
      ),
    ).toBe(false);
  });

  it("rejects owner assignment when manager holds settings or platform access", () => {
    const input = { isOwner: true, livePermissions: live, roles: catalog };

    expect(
      roleIsAssignable(
        { ...managerRole, permission_codes: ["settings.manage"] },
        input,
      ),
    ).toBe(false);
    expect(
      roleIsAssignable(
        { ...managerRole, permission_codes: ["platform.gyms.view"] },
        input,
      ),
    ).toBe(false);
  });

  it("requires an owner when an assigned role is outside the caller boundary", () => {
    expect(
      assignmentRequiresOwner([managerRole, receptionist], catalog, {
        isOwner: false,
        livePermissions: live,
      }),
    ).toBe(true);
    expect(
      assignmentRequiresOwner([receptionist], catalog, {
        isOwner: false,
        livePermissions: live,
      }),
    ).toBe(false);
    expect(
      assignmentRequiresOwner([managerRole], catalog, {
        isOwner: true,
        livePermissions: [],
      }),
    ).toBe(false);
  });
});

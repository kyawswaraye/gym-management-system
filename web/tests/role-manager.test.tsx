import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  archiveRole,
  createRole,
  listAllPermissions,
  listAllRoles,
  replaceRolePermissions,
  updateRole,
} from "@/features/operations/client";
import { RoleManager } from "@/features/operations/role-manager";
import {
  HARD_RESERVED_APPROVALS,
  HARD_RESERVED_LABEL,
  MANAGER_REQUIRED_LABEL,
  SETTINGS_MANAGE_LABEL,
} from "@/features/operations/role-policy";
import type { Permission, Role } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  archiveRole: vi.fn(),
  createRole: vi.fn(),
  listAllPermissions: vi.fn(),
  listAllRoles: vi.fn(),
  replaceRolePermissions: vi.fn(),
  updateRole: vi.fn(),
}));

function role(overrides: Partial<Role> = {}): Role {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    key: "FLOOR",
    name: "Floor",
    description: "Floor team",
    is_active: true,
    is_protected: false,
    is_template: false,
    source_template_id: null,
    source_template_version: null,
    permission_codes: ["members.view"],
    permission_count: 1,
    assignment_count: 3,
    created_by_membership_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

const floor = role();
const desk = role({
  id: "55555555-5555-4555-8555-555555555555",
  key: "DESK",
  name: "Desk",
  permission_codes: ["members.create"],
  permission_count: 1,
  assignment_count: 0,
});
const managerRole = role({
  id: "33333333-3333-4333-8333-333333333333",
  key: "MANAGER",
  name: "Manager",
  description: "Runs the gym",
  is_protected: true,
  is_template: true,
  permission_codes: [
    "members.view",
    "members.create",
    "roles.manage",
    ...HARD_RESERVED_APPROVALS,
    "settings.manage",
  ],
  permission_count: 9,
  assignment_count: 2,
});
const ownerRole = role({
  id: "22222222-2222-4222-8222-222222222222",
  key: "OWNER",
  name: "Owner",
  is_protected: true,
  is_template: true,
  permission_codes: ["settings.manage"],
  permission_count: 1,
  assignment_count: 1,
});
const receptionist = role({
  id: "66666666-6666-4666-8666-666666666666",
  key: "RECEPTIONIST",
  name: "Receptionist",
  is_protected: true,
  is_template: true,
  assignment_count: 4,
});

function permission(code: string, module = "members"): Permission {
  return {
    id: `00000000-0000-4000-8000-${code.replaceAll(".", "").padEnd(12, "0").slice(0, 12)}`,
    code,
    module,
    description: `${code} description`,
    risk_level: "LOW",
    is_active: code !== "reports.view",
  };
}

const catalog = [
  permission("members.view"),
  permission("members.create"),
  permission("roles.manage", "roles"),
  permission("payments.approve", "payments"),
  permission("settings.manage", "settings"),
  permission("platform.gyms.view", "platform"),
  permission("reports.view", "reports"),
];

const roles = [ownerRole, managerRole, receptionist, floor, desk];
const reader = {
  canManage: false,
  isOwner: false,
  permissions: ["roles.view"],
};
const manager = {
  canManage: true,
  isOwner: false,
  permissions: ["roles.view", "roles.manage", "members.view"],
};
const owner = {
  canManage: true,
  isOwner: true,
  permissions: ["roles.view", "roles.manage", "settings.manage"],
};

describe("Role manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listAllRoles).mockResolvedValue(roles);
    vi.mocked(listAllPermissions).mockResolvedValue(catalog);
    vi.mocked(createRole).mockResolvedValue(floor);
    vi.mocked(updateRole).mockResolvedValue(floor);
    vi.mocked(archiveRole).mockResolvedValue({ ...floor, is_active: false });
    vi.mocked(replaceRolePermissions).mockResolvedValue(floor);
  });

  it("shows a loading status, then an empty catalog", async () => {
    let resolveRoles: (value: Role[]) => void = () => undefined;
    vi.mocked(listAllRoles).mockReturnValue(
      new Promise((resolve) => {
        resolveRoles = resolve;
      }),
    );
    vi.mocked(listAllPermissions).mockResolvedValue([]);
    render(<RoleManager {...reader} />);

    expect(screen.getByText("Loading roles.")).toBeInTheDocument();
    resolveRoles([]);

    expect(
      await screen.findByText("No roles are defined for this gym."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No permissions are in the catalog."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create role" }),
    ).not.toBeInTheDocument();
  });

  it("shows a role load error", async () => {
    vi.mocked(listAllRoles).mockRejectedValue(
      new Error("Unable to load roles."),
    );
    render(<RoleManager {...reader} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to load roles.",
    );
  });

  it("shows protected and custom roles without mutation controls", async () => {
    render(<RoleManager {...reader} />);

    const floorCard = within(
      await screen.findByRole("article", { name: "Floor" }),
    );
    expect(floorCard.getByText("Custom")).toBeInTheDocument();
    expect(floorCard.getByText("Active")).toBeInTheDocument();
    expect(floorCard.getByText("3")).toBeInTheDocument();
    expect(floorCard.getByText("members.view")).toBeInTheDocument();
    expect(floorCard.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      floorCard.queryByRole("button", { name: "Archive Floor" }),
    ).not.toBeInTheDocument();

    const ownerCard = within(screen.getByRole("article", { name: "Owner" }));
    expect(ownerCard.getByText("Protected")).toBeInTheDocument();
    expect(ownerCard.getByText("1")).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("region", { name: "Permission catalog" }),
      ).getByText(HARD_RESERVED_LABEL),
    ).toBeInTheDocument();
    expect(screen.getByText(SETTINGS_MANAGE_LABEL)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create role" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("limits a non-owner manager to in-boundary custom role controls", async () => {
    render(<RoleManager {...manager} />);

    const floorCard = within(
      await screen.findByRole("article", { name: "Floor" }),
    );
    const permissions = within(
      floorCard.getByRole("group", { name: "Permissions for Floor" }),
    );
    expect(
      permissions.getByRole("checkbox", { name: "members.view" }),
    ).toBeEnabled();
    expect(
      permissions.queryByRole("checkbox", { name: "members.create" }),
    ).not.toBeInTheDocument();
    expect(
      permissions.queryByRole("checkbox", { name: "payments.approve" }),
    ).not.toBeInTheDocument();
    expect(
      permissions.queryByRole("checkbox", { name: "settings.manage" }),
    ).not.toBeInTheDocument();
    expect(permissions.getAllByText(HARD_RESERVED_LABEL)).toHaveLength(
      HARD_RESERVED_APPROVALS.length,
    );
    expect(permissions.getByText(SETTINGS_MANAGE_LABEL)).toBeInTheDocument();
    expect(permissions.queryByRole("textbox")).not.toBeInTheDocument();

    expect(
      within(screen.getByRole("article", { name: "Desk" })).getByText(
        "This role is outside your delegation boundary.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Desk" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Manager" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save permissions for Manager" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Owner" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      permissions.getByRole("button", { name: "Save permissions for Floor" }),
    );
    await waitFor(() =>
      expect(replaceRolePermissions).toHaveBeenCalledWith(floor.id, [
        "members.view",
      ]),
    );
  });

  it("lets an owner replace manager policy without changing protected identity", async () => {
    const prompt = vi.spyOn(window, "prompt").mockImplementation(() => {
      throw new Error("browser prompt");
    });
    const confirm = vi.spyOn(window, "confirm").mockImplementation(() => {
      throw new Error("browser confirm");
    });
    render(<RoleManager {...owner} />);

    const managerCard = within(
      await screen.findByRole("article", { name: "Manager" }),
    );
    expect(
      managerCard.queryByRole("button", { name: "Archive Manager" }),
    ).not.toBeInTheDocument();
    expect(
      managerCard.queryByRole("button", { name: "Save Manager" }),
    ).not.toBeInTheDocument();
    const managerPermissions = within(
      managerCard.getByRole("group", { name: "Permissions for Manager" }),
    );
    const approval = managerPermissions.getByRole("checkbox", {
      name: "payments.approve",
    });
    expect(approval).toBeDisabled();
    expect(approval).toBeChecked();
    expect(
      managerPermissions.getAllByText(MANAGER_REQUIRED_LABEL),
    ).toHaveLength(HARD_RESERVED_APPROVALS.length);
    expect(
      managerPermissions.queryByRole("checkbox", { name: "settings.manage" }),
    ).not.toBeInTheDocument();
    expect(
      managerPermissions.getByText(SETTINGS_MANAGE_LABEL),
    ).toBeInTheDocument();
    expect(
      managerPermissions.queryByRole("checkbox", {
        name: "platform.gyms.view",
      }),
    ).not.toBeInTheDocument();
    expect(
      managerPermissions.queryByRole("checkbox", { name: "reports.view" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      managerPermissions.getByRole("button", {
        name: "Save permissions for Manager",
      }),
    );
    await waitFor(() => expect(replaceRolePermissions).toHaveBeenCalled());
    const codes = vi.mocked(replaceRolePermissions).mock.calls[0]?.[1] ?? [];
    expect(codes).toEqual(
      expect.arrayContaining([
        ...HARD_RESERVED_APPROVALS,
        "members.view",
        "members.create",
        "roles.manage",
      ]),
    );
    expect(codes).toHaveLength(HARD_RESERVED_APPROVALS.length + 3);
    expect(codes).not.toContain("settings.manage");
    expect(codes).not.toContain("platform.gyms.view");

    const ownerCard = within(screen.getByRole("article", { name: "Owner" }));
    expect(ownerCard.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      ownerCard.queryByRole("button", { name: /Save|Archive/ }),
    ).not.toBeInTheDocument();

    const receptionistCard = within(
      screen.getByRole("article", { name: "Receptionist" }),
    );
    expect(
      receptionistCard.queryByRole("button", { name: "Archive Receptionist" }),
    ).not.toBeInTheDocument();
    expect(
      receptionistCard.getByRole("button", {
        name: "Save permissions for Receptionist",
      }),
    ).toBeInTheDocument();
    expect(
      within(
        receptionistCard.getByRole("group", {
          name: "Permissions for Receptionist",
        }),
      ).queryByRole("checkbox", { name: "payments.approve" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Archive Floor" }));
    const dialog = screen.getByRole("dialog", { name: "Archive Floor" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(archiveRole).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Archive Floor" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Archive Floor" })).getByRole(
        "button",
        { name: "Confirm archive" },
      ),
    );
    await waitFor(() => expect(archiveRole).toHaveBeenCalledWith(floor.id));
    expect(await screen.findByText("Floor archived.")).toBeInTheDocument();
    expect(prompt).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("creates a custom role without permission ids", async () => {
    render(<RoleManager {...manager} />);
    const create = within(
      await screen.findByRole("region", { name: "Create role" }),
    );
    fireEvent.click(create.getByRole("button", { name: "Create role" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter an uppercase role key",
    );
    expect(createRole).not.toHaveBeenCalled();

    fireEvent.change(create.getByLabelText("Key"), {
      target: { value: "OWNER" },
    });
    fireEvent.change(create.getByLabelText("Name"), {
      target: { value: "Not an owner" },
    });
    fireEvent.click(create.getByRole("button", { name: "Create role" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This role key is reserved.",
    );

    fireEvent.change(create.getByLabelText("Key"), {
      target: { value: "GUIDE" },
    });
    fireEvent.change(create.getByLabelText("Name"), {
      target: { value: "Guide" },
    });
    fireEvent.change(create.getByLabelText("Description"), {
      target: { value: "Helps members" },
    });
    fireEvent.click(create.getByRole("button", { name: "Create role" }));

    await waitFor(() =>
      expect(createRole).toHaveBeenCalledWith({
        key: "GUIDE",
        name: "Guide",
        description: "Helps members",
        is_active: true,
      }),
    );
    expect(vi.mocked(createRole).mock.calls[0]?.[0]).not.toHaveProperty(
      "permission_codes",
    );
    expect(await screen.findByText("Role created.")).toBeInTheDocument();
  });
});

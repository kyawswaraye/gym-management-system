import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  activateStaff,
  getMembershipRoles,
  getStaff,
  inviteStaff,
  listActiveRoleCatalog,
  listStaff,
  replaceMembershipRoles,
  suspendStaff,
  updateStaff,
} from "@/features/operations/client";
import { StaffManager } from "@/features/operations/staff-manager";
import type { StaffListPage } from "@/features/operations/client";
import type { Role, StaffRecord } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  activateStaff: vi.fn(),
  getMembershipRoles: vi.fn(),
  getStaff: vi.fn(),
  inviteStaff: vi.fn(),
  listActiveRoleCatalog: vi.fn(),
  listStaff: vi.fn(),
  replaceMembershipRoles: vi.fn(),
  suspendStaff: vi.fn(),
  updateStaff: vi.fn(),
}));

function role(overrides: Partial<Role> = {}): Role {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    key: "TRAINER",
    name: "Trainer",
    description: "Runs sessions",
    is_active: true,
    is_protected: true,
    is_template: true,
    source_template_id: null,
    source_template_version: null,
    permission_codes: ["members.view"],
    permission_count: 1,
    assignment_count: 1,
    created_by_membership_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

const trainerRole = role();
const ownerRole = role({
  id: "22222222-2222-4222-8222-222222222222",
  key: "OWNER",
  name: "Owner",
  description: "Protected owner role",
});
const deskRole = role({
  id: "44444444-4444-4444-8444-444444444444",
  key: "DESK",
  name: "Desk",
  description: "Front desk",
  is_protected: false,
  is_template: false,
});
const managerRole = role({
  id: "55555555-5555-4555-8555-555555555555",
  key: "MANAGER",
  name: "Manager",
  description: "Runs the gym",
  is_protected: true,
  is_template: false,
  permission_codes: [
    "staff.view",
    "staff.manage",
    "roles.view",
    "members.view",
    "payments.approve",
    "expenses.approve",
    "retail.approve",
    "pt.sales.approve",
    "salaries.approve",
  ],
  permission_count: 9,
});
const reservedRole = role({
  id: "66666666-6666-4666-8666-666666666666",
  key: "APPROVER",
  name: "Approver",
  description: "Reserved approval",
  is_protected: false,
  is_template: false,
  permission_codes: ["members.view", "payments.approve"],
});
const settingsRole = role({
  id: "77777777-7777-4777-8777-777777777777",
  key: "CONFIG",
  name: "Config",
  description: "Owner settings",
  is_protected: false,
  is_template: false,
  permission_codes: ["settings.manage"],
});
const platformRole = role({
  id: "88888888-8888-4888-8888-888888888888",
  key: "OPS",
  name: "Platform ops",
  description: "Platform access",
  is_protected: false,
  is_template: false,
  permission_codes: ["platform.gyms.view"],
});

function staffPage(
  data: StaffRecord[],
  nextAfter: string | null = null,
): StaffListPage {
  return {
    data,
    meta: { has_more: nextAfter !== null, next_after: nextAfter },
  };
}

function staff(overrides: Partial<StaffRecord> = {}): StaffRecord {
  return {
    id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
    staff_code: "TR-1",
    display_name: "Ada Trainer",
    phone: "555",
    joined_date: "2026-01-15",
    status: "ACTIVE",
    employment_title: "Coach",
    default_commission_percent: "10.00",
    terminated_date: null,
    archived_at: null,
    membership: {
      gym_membership_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "ada@example.test",
      first_name: "Ada",
      last_name: "Trainer",
      status: "ACTIVE",
      is_owner: false,
      authz_version: 2,
      invited_at: "2026-01-01T00:00:00Z",
      activated_at: "2026-01-15T00:00:00Z",
    },
    role_ids: [trainerRole.id],
    invited_role_ids: [],
    ...overrides,
  };
}

const viewOnly = {
  canView: true,
  canManage: false,
  canViewRoles: true,
  isOwner: false,
  permissions: ["staff.view", "roles.view"],
};

const manager = {
  canView: true,
  canManage: true,
  canViewRoles: true,
  isOwner: false,
  permissions: ["staff.view", "staff.manage", "roles.view", "members.view"],
};

describe("Staff manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listStaff).mockResolvedValue(staffPage([staff()]));
    vi.mocked(getStaff).mockImplementation(async () => staff());
    vi.mocked(listActiveRoleCatalog).mockResolvedValue([
      ownerRole,
      managerRole,
      trainerRole,
      deskRole,
    ]);
    vi.mocked(getMembershipRoles).mockResolvedValue({
      gym_membership_id: staff().membership.gym_membership_id,
      roles: [trainerRole],
      authz_version: 2,
    });
    vi.mocked(replaceMembershipRoles).mockResolvedValue({
      gym_membership_id: staff().membership.gym_membership_id,
      roles: [trainerRole, deskRole],
      authz_version: 5,
    });
    vi.mocked(inviteStaff).mockResolvedValue({
      id: "33333333-3333-4333-8333-333333333333",
      email: "new@example.test",
      status: "PENDING",
      expires_at: "2026-10-20T00:00:00Z",
      role_ids: [trainerRole.id],
      membership: staff().membership,
      staff: staff(),
    });
    vi.mocked(updateStaff).mockResolvedValue(
      staff({ display_name: "Ada Coach" }),
    );
    vi.mocked(suspendStaff).mockResolvedValue(staff());
    vi.mocked(activateStaff).mockResolvedValue(staff());
  });

  it("shows a loading status and then an empty staff list", async () => {
    let resolveList: (value: StaffListPage) => void = () => undefined;
    vi.mocked(listStaff).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    render(<StaffManager {...viewOnly} canViewRoles={false} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading staff.");
    resolveList(staffPage([]));

    expect(
      await screen.findByText("No staff records match this view."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
    expect(listStaff).toHaveBeenCalledWith({ limit: 25 });
    expect(listActiveRoleCatalog).not.toHaveBeenCalled();
    expect(getMembershipRoles).not.toHaveBeenCalled();
  });

  it("shows safe records without mutation controls", async () => {
    render(<StaffManager {...viewOnly} />);

    expect(await screen.findByText("ada@example.test")).toBeInTheDocument();
    expect(screen.getByText("Ada Trainer")).toBeInTheDocument();
    expect(listActiveRoleCatalog).not.toHaveBeenCalled();
    expect(getMembershipRoles).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "View details for Ada Trainer" }),
    );

    expect(await screen.findByText("Coach")).toBeInTheDocument();
    expect(screen.getByText("10.00")).toBeInTheDocument();
    expect(screen.getByText("Assigned roles")).toBeInTheDocument();
    expect(screen.queryByText("2")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(getMembershipRoles).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Save assigned roles" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save staff profile" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Suspend access" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Activate access" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /terminate|archive|owner|photo/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/owner|photo|permission/i),
    ).not.toBeInTheDocument();
  });

  it("does not invite when role choices cannot be viewed", async () => {
    render(<StaffManager {...manager} canViewRoles={false} />);

    expect(
      await screen.findByText(
        "Inviting staff requires permission to view roles.",
      ),
    ).toBeInTheDocument();
    expect(listActiveRoleCatalog).not.toHaveBeenCalled();
    expect(getMembershipRoles).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: "Invite staff" }),
    ).not.toBeInTheDocument();
  });

  it("invites a non-owner role and reports field errors", async () => {
    render(<StaffManager {...manager} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Invite staff" }),
    );
    expect(
      await screen.findByRole("checkbox", { name: "Trainer" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Owner" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));

    const invite = within(screen.getByRole("dialog", { name: "Invite staff" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid email address.",
    );
    expect(inviteStaff).not.toHaveBeenCalled();

    fireEvent.change(invite.getByLabelText("Email"), {
      target: { value: "new@example.test" },
    });
    fireEvent.change(invite.getByLabelText("First name"), {
      target: { value: "New" },
    });
    fireEvent.change(invite.getByLabelText("Last name"), {
      target: { value: "Coach" },
    });
    fireEvent.change(invite.getByLabelText("Display name"), {
      target: { value: "New Coach" },
    });
    fireEvent.change(invite.getByLabelText("Joined date"), {
      target: { value: "2026-10-17" },
    });
    fireEvent.change(invite.getByLabelText("Default commission percent"), {
      target: { value: "5.50" },
    });
    fireEvent.click(invite.getByRole("checkbox", { name: "Trainer" }));
    vi.mocked(inviteStaff).mockRejectedValueOnce(
      Object.assign(new Error("Enter a valid email address."), {
        fields: { email: "Enter a valid email address." },
      }),
    );
    fireEvent.click(invite.getByRole("button", { name: "Send invitation" }));

    await waitFor(() => expect(inviteStaff).toHaveBeenCalledTimes(1));
    expect(inviteStaff).toHaveBeenCalledWith({
      email: "new@example.test",
      first_name: "New",
      last_name: "Coach",
      staff_code: "",
      display_name: "New Coach",
      joined_date: "2026-10-17",
      default_commission_percent: "5.50",
      role_ids: [trainerRole.id],
    });

    vi.mocked(inviteStaff).mockResolvedValueOnce({
      id: "33333333-3333-4333-8333-333333333333",
      email: "new@example.test",
      status: "PENDING",
      expires_at: "2026-10-20T00:00:00Z",
      role_ids: [trainerRole.id],
      membership: staff().membership,
      staff: staff(),
    });
    fireEvent.click(invite.getByRole("button", { name: "Send invitation" }));

    expect(
      await screen.findByText("Invitation sent to new@example.test."),
    ).toBeInTheDocument();
  });

  it("filters the staff list", async () => {
    render(<StaffManager {...viewOnly} canViewRoles={false} />);
    expect(await screen.findByText("Ada Trainer")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search staff"), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByLabelText("Profile status"), {
      target: { value: "ACTIVE" },
    });
    fireEvent.change(screen.getByLabelText("Membership status"), {
      target: { value: "SUSPENDED" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() =>
      expect(listStaff).toHaveBeenCalledWith({
        limit: 25,
        search: "Ada",
        status: "ACTIVE",
        membership_status: "SUSPENDED",
      }),
    );
  });

  it("resets the cursor when filters change and appends the next page once", async () => {
    const ada = staff();
    const bea = staff({
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01",
      staff_code: "RC-1",
      display_name: "Bea Coach",
      membership: { ...staff().membership, email: "bea@example.test" },
    });
    const cara = staff({
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02",
      staff_code: "RC-2",
      display_name: "Cara Desk",
      membership: { ...staff().membership, email: "cara@example.test" },
    });
    vi.mocked(listStaff).mockImplementation(async (query) => {
      if (query?.after) {
        return staffPage([bea, cara]);
      }
      if (query?.search) {
        return staffPage([bea], "cursor.page-2");
      }
      return staffPage([ada], "cursor.page-1");
    });
    render(<StaffManager {...viewOnly} canViewRoles={false} />);

    expect(await screen.findByText("Ada Trainer")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Load more" }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search staff"), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByLabelText("Profile status"), {
      target: { value: "ACTIVE" },
    });
    fireEvent.change(screen.getByLabelText("Membership status"), {
      target: { value: "SUSPENDED" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByText("Bea Coach")).toBeInTheDocument();
    expect(screen.queryByText("Ada Trainer")).not.toBeInTheDocument();
    expect(listStaff).toHaveBeenLastCalledWith({
      limit: 25,
      search: "Ada",
      status: "ACTIVE",
      membership_status: "SUSPENDED",
    });

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Cara Desk")).toBeInTheDocument();
    expect(screen.getAllByText("Bea Coach")).toHaveLength(1);
    expect(listStaff).toHaveBeenLastCalledWith({
      limit: 25,
      search: "Ada",
      status: "ACTIVE",
      membership_status: "SUSPENDED",
      after: "cursor.page-2",
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Load more" }),
      ).not.toBeInTheDocument(),
    );
    expect(document.body.textContent).not.toContain("cursor.page");
  });

  it("saves profile fields and suspends only after a reason", async () => {
    const prompt = vi.spyOn(window, "prompt").mockImplementation(() => {
      throw new Error("browser prompt");
    });
    const confirm = vi.spyOn(window, "confirm").mockImplementation(() => {
      throw new Error("browser confirm");
    });
    render(<StaffManager {...manager} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View details for Ada Trainer",
      }),
    );
    const detail = within(
      await screen.findByRole("region", { name: "Ada Trainer" }),
    );
    fireEvent.click(detail.getByRole("button", { name: "Edit profile" }));
    fireEvent.change(detail.getByLabelText("Display name"), {
      target: { value: "Ada Coach" },
    });
    fireEvent.click(detail.getByRole("button", { name: "Save staff profile" }));

    await waitFor(() =>
      expect(updateStaff).toHaveBeenCalledWith(staff().id, {
        display_name: "Ada Coach",
        phone: "555",
        joined_date: "2026-01-15",
        employment_title: "Coach",
        default_commission_percent: "10.00",
      }),
    );
    expect(
      await screen.findByText("Staff profile updated."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Suspend access" }));
    const dialog = screen.getByRole("dialog", { name: "Suspend staff access" });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm suspension" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a reason of at least 3 characters.",
    );
    expect(suspendStaff).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Seasonal leave" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm suspension" }),
    );

    await waitFor(() =>
      expect(suspendStaff).toHaveBeenCalledWith(staff().id, "Seasonal leave"),
    );
    expect(
      await screen.findByText("Staff access suspended."),
    ).toBeInTheDocument();
    expect(prompt).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("activates suspended staff and hides access changes after termination", async () => {
    vi.mocked(listStaff).mockResolvedValue(
      staffPage([
        staff({
          membership: { ...staff().membership, status: "SUSPENDED" },
        }),
      ]),
    );
    vi.mocked(getStaff).mockResolvedValue(
      staff({
        membership: { ...staff().membership, status: "SUSPENDED" },
      }),
    );
    render(<StaffManager {...manager} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View details for Ada Trainer",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Activate access" }),
    );
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "Cleared to return" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm activation" }));

    await waitFor(() =>
      expect(activateStaff).toHaveBeenCalledWith(
        staff().id,
        "Cleared to return",
      ),
    );

    vi.mocked(getStaff).mockResolvedValue(
      staff({ status: "TERMINATED", terminated_date: "2026-10-01" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "View details for Ada Trainer" }),
    );

    expect(await screen.findByText("2026-10-01")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Suspend access" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Activate access" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /terminate|archive/i }),
    ).not.toBeInTheDocument();
  });

  it("replaces non-owner membership roles and shows the new authorization version", async () => {
    const prompt = vi.spyOn(window, "prompt").mockImplementation(() => {
      throw new Error("browser prompt");
    });
    render(<StaffManager {...manager} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View details for Ada Trainer",
      }),
    );

    const assignment = within(
      await screen.findByRole("region", { name: "Assign roles" }),
    );
    expect(getMembershipRoles).toHaveBeenCalledWith(
      staff().membership.gym_membership_id,
    );
    expect(
      await assignment.findByText("Authorization version 2"),
    ).toBeInTheDocument();
    fireEvent.click(
      assignment.getByRole("button", { name: "Edit assigned roles" }),
    );
    expect(
      await assignment.findByRole("checkbox", { name: "Trainer" }),
    ).toBeChecked();
    expect(
      assignment.queryByRole("checkbox", { name: "Owner" }),
    ).not.toBeInTheDocument();
    expect(assignment.queryByRole("textbox")).not.toBeInTheDocument();

    fireEvent.click(assignment.getByRole("checkbox", { name: "Trainer" }));
    fireEvent.click(
      assignment.getByRole("button", { name: "Save assigned roles" }),
    );
    expect(await assignment.findByRole("alert")).toHaveTextContent(
      "Select at least one role.",
    );
    expect(replaceMembershipRoles).not.toHaveBeenCalled();

    fireEvent.click(assignment.getByRole("checkbox", { name: "Trainer" }));
    fireEvent.click(assignment.getByRole("checkbox", { name: "Desk" }));
    fireEvent.click(
      assignment.getByRole("button", { name: "Save assigned roles" }),
    );

    await waitFor(() =>
      expect(replaceMembershipRoles).toHaveBeenCalledWith(
        staff().membership.gym_membership_id,
        [trainerRole.id, deskRole.id],
      ),
    );
    expect(
      await assignment.findByText("Authorization version 5"),
    ).toBeInTheDocument();
    expect(
      assignment.getByText("Roles saved. Authorization version 5."),
    ).toBeInTheDocument();
    expect(prompt).not.toHaveBeenCalled();
  });

  it("hides assignment controls when the manager cannot view roles", async () => {
    render(<StaffManager {...manager} canViewRoles={false} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View details for Ada Trainer",
      }),
    );

    expect(
      await screen.findByText(
        "Assigning roles requires permission to view roles.",
      ),
    ).toBeInTheDocument();
    expect(getMembershipRoles).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Save assigned roles" }),
    ).not.toBeInTheDocument();
  });

  it("offers a manager only receptionist and custom roles inside the live boundary", async () => {
    vi.mocked(listActiveRoleCatalog).mockResolvedValue([
      ownerRole,
      managerRole,
      trainerRole,
      deskRole,
      reservedRole,
      settingsRole,
      platformRole,
    ]);
    render(<StaffManager {...manager} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Invite staff" }),
    );
    const invite = within(
      await screen.findByRole("dialog", { name: "Invite staff" }),
    );
    expect(
      await invite.findByRole("checkbox", { name: "Trainer" }),
    ).toBeInTheDocument();
    expect(invite.getByRole("checkbox", { name: "Desk" })).toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Manager" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Approver" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Config" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Platform ops" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Owner" }),
    ).not.toBeInTheDocument();
  });

  it("lets an owner assign protected manager and in-policy roles", async () => {
    vi.mocked(listActiveRoleCatalog).mockResolvedValue([
      ownerRole,
      managerRole,
      trainerRole,
      deskRole,
      reservedRole,
      settingsRole,
      platformRole,
    ]);
    render(<StaffManager {...manager} isOwner />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Invite staff" }),
    );
    const invite = within(
      await screen.findByRole("dialog", { name: "Invite staff" }),
    );
    expect(
      await invite.findByRole("checkbox", { name: "Manager" }),
    ).toBeInTheDocument();
    expect(
      invite.getByRole("checkbox", { name: "Trainer" }),
    ).toBeInTheDocument();
    expect(invite.getByRole("checkbox", { name: "Desk" })).toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Approver" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Config" }),
    ).not.toBeInTheDocument();
    expect(
      invite.queryByRole("checkbox", { name: "Platform ops" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty invitation state when nothing is assignable", async () => {
    render(
      <StaffManager
        {...manager}
        permissions={["staff.view", "staff.manage", "roles.view"]}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Invite staff" }),
    );
    expect(
      await screen.findByText("No assignable roles are available."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("keeps an assigned out-of-boundary role read-only", async () => {
    vi.mocked(getMembershipRoles).mockResolvedValue({
      gym_membership_id: staff().membership.gym_membership_id,
      roles: [managerRole, trainerRole],
      authz_version: 2,
    });
    render(<StaffManager {...manager} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View details for Ada Trainer",
      }),
    );

    const assignment = within(
      await screen.findByRole("region", { name: "Assign roles" }),
    );
    const retained = await assignment.findByRole("checkbox", {
      name: /Manager/,
    });
    expect(retained).toBeDisabled();
    expect(retained).toBeChecked();
    expect(
      assignment.getByText("Outside your delegation boundary."),
    ).toBeInTheDocument();
    expect(assignment.getByText("Trainer")).toBeInTheDocument();
    expect(
      assignment.getByText(
        "An Owner is required to change this role assignment.",
      ),
    ).toBeInTheDocument();
    expect(
      assignment.queryByRole("checkbox", { name: "Trainer" }),
    ).not.toBeInTheDocument();
    expect(
      assignment.queryByRole("button", { name: "Save assigned roles" }),
    ).not.toBeInTheDocument();
    expect(replaceMembershipRoles).not.toHaveBeenCalled();
  });
});

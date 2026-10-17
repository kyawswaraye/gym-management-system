import type { ComponentProps } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  listAllMembershipPlans,
  listMembers,
  listMembershipExpiry,
  listMembershipPlans,
  listMemberships,
  operationMutation,
} from "@/features/operations/client";
import { MembershipsManager } from "@/features/operations/memberships-manager";
import type {
  Member,
  MemberListPage,
  Membership,
  MembershipListPage,
  MembershipPlan,
} from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  listAllMembershipPlans: vi.fn(),
  listMembers: vi.fn(),
  listMembershipExpiry: vi.fn(),
  listMembershipPlans: vi.fn(),
  listMemberships: vi.fn(),
  operationMutation: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const LATER_MEMBER_ID = "55555555-5555-4555-8555-555555555555";
const PLAN_ID = "22222222-2222-4222-8222-222222222222";
const LATER_PLAN_ID = "66666666-6666-4666-8666-666666666666";
const MEMBERSHIP_ID = "33333333-3333-4333-8333-333333333333";
const LATER_MEMBERSHIP_ID = "77777777-7777-4777-8777-777777777777";

function member(overrides: Partial<Member> = {}): Member {
  return {
    id: MEMBER_ID,
    member_number: "MEM-000001",
    first_name: "Ada",
    last_name: "Lovelace",
    normalized_full_name: "ada lovelace",
    phone: "555",
    email: "ada@example.test",
    gender: null,
    date_of_birth: null,
    address: {
      address_line1: "",
      address_line2: "",
      city: "",
      region: "",
      postal_code: "",
      country_code: "",
    },
    join_date: "2026-01-01",
    emergency_contact_name: "",
    emergency_contact_phone: "",
    notes: "",
    profile_photo_asset_id: null,
    linked_user_id: null,
    is_pt_client: false,
    authz_version: 1,
    status: "ACTIVE",
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function plan(overrides: Partial<MembershipPlan> = {}): MembershipPlan {
  return {
    id: PLAN_ID,
    code: "MONTH",
    name: "Monthly",
    description: "",
    duration_value: 1,
    duration_unit: "MONTH",
    price: "1500.00",
    currency: "THB",
    is_active: true,
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function membership(overrides: Partial<Membership> = {}): Membership {
  return {
    id: MEMBERSHIP_ID,
    member_id: MEMBER_ID,
    plan_id: PLAN_ID,
    plan_code: "MONTH",
    plan_name: "Monthly",
    duration_value: 1,
    duration_unit: "MONTH",
    start_date: "2026-01-31",
    expiry_date: "2026-02-27",
    list_price: "1500.00",
    discount_amount: "100.50",
    final_amount: "1399.50",
    currency: "THB",
    payment_status: "UNPAID",
    status: "ACTIVE",
    note: "Desk note",
    cancelled_at: null,
    cancelled_by_membership_id: null,
    cancellation_reason: "",
    created_by_membership_id: "44444444-4444-4444-8444-444444444444",
    created_at: "2026-01-31T00:00:00Z",
    updated_at: "2026-01-31T00:00:00Z",
    ...overrides,
  };
}

function membershipPage(
  rows: Membership[],
  next: string | null = null,
): MembershipListPage {
  return {
    data: rows,
    meta: { has_more: next !== null, next_after: next },
  };
}

function memberPage(rows: Member[]): MemberListPage {
  return { data: rows, meta: { has_more: false, next_after: null } };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderManager(
  props: Partial<ComponentProps<typeof MembershipsManager>> = {},
) {
  render(
    <MembershipsManager
      canCancel
      canCreate
      canUpdate
      currency="THB"
      defaultDate="2026-10-17"
      {...props}
    />,
  );
}

describe("Memberships manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMemberships).mockResolvedValue(
      membershipPage([membership()]),
    );
    vi.mocked(listMembershipExpiry).mockResolvedValue(membershipPage([]));
    vi.mocked(listAllMembershipPlans).mockResolvedValue([plan()]);
    vi.mocked(listMembers).mockResolvedValue(memberPage([member()]));
    vi.mocked(operationMutation).mockResolvedValue({});
  });

  afterEach(() => {
    if (vi.isMockFunction(window.confirm)) {
      vi.mocked(window.confirm).mockRestore();
    }
  });

  it("renders published plan and membership fields and the member id until search", async () => {
    renderManager();

    expect(
      await screen.findByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("ACTIVE / UNPAID")).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: "Memberships" })).getByRole(
        "cell",
        { name: "Monthly" },
      ),
    ).toBeInTheDocument();
    const planCatalog = screen.getByRole("table", { name: "Membership plans" });
    expect(
      within(planCatalog).getByRole("cell", { name: "MONTH" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Monthly" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "1 Month" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "1500.00 THB" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Active" }),
    ).toBeInTheDocument();
    expect(screen.getByText(MEMBER_ID)).toBeInTheDocument();
    expect(screen.getByText("2026-01-31 – 2026-02-27")).toBeInTheDocument();
    expect(screen.getByText("1399.50 THB")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE / UNPAID")).toBeInTheDocument();
    expect(screen.getByLabelText("Currency")).toHaveValue("THB");
    expect(screen.getByLabelText("Currency")).not.toHaveAttribute("name");
    expect(screen.queryByText("ada lovelace")).not.toBeInTheDocument();
    expect(listMembers).not.toHaveBeenCalled();
    expect(listMemberships).toHaveBeenCalledWith({ limit: 25 });
    expect(listMembershipPlans).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Ada" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "MEM-000001 — Ada Lovelace" }),
    );
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(
      screen.getByText("Selected member: MEM-000001 — Ada Lovelace"),
    ).toBeInTheDocument();
  });

  it("creates a plan without sending currency and refreshes the catalogs", async () => {
    renderManager();
    await screen.findByRole("option", { name: "Monthly — 1500.00 THB" });
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Code"), {
      target: { value: "MONTH" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Monthly" },
    });
    fireEvent.change(screen.getByLabelText("Duration"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("Price"), {
      target: { value: "1500.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith("membership-plans/", {
        code: "MONTH",
        name: "Monthly",
        duration_value: 1,
        duration_unit: "MONTH",
        price: "1500.00",
      }),
    );
    await waitFor(() =>
      expect(listAllMembershipPlans).toHaveBeenCalledTimes(2),
    );
    expect(vi.mocked(listMemberships).mock.calls.at(-1)?.[0]).toEqual({
      limit: 25,
    });
  });

  it("searches past the first member page and does not auto-select", async () => {
    const zara = member({
      id: LATER_MEMBER_ID,
      member_number: "MEM-000090",
      first_name: "Zara",
      last_name: "Khan",
    });
    vi.mocked(listMembers).mockImplementation(async (query = {}) => {
      if (query.search === "Zara") return memberPage([zara]);
      return memberPage([member()]);
    });
    renderManager();
    await screen.findByRole("option", { name: "Monthly — 1500.00 THB" });

    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Zara" },
    });
    expect(screen.getByText("No member selected.")).toBeInTheDocument();
    const choice = await screen.findByRole("button", {
      name: "MEM-000090 — Zara Khan",
    });
    expect(screen.getByText("No member selected.")).toBeInTheDocument();
    expect(listMembers).toHaveBeenCalledWith({
      search: "Zara",
      status: "ACTIVE",
      limit: 25,
    });

    fireEvent.change(screen.getByLabelText("Plan"), {
      target: { value: PLAN_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start membership" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select a member.",
    );
    expect(operationMutation).not.toHaveBeenCalled();

    fireEvent.click(choice);
    fireEvent.change(screen.getByLabelText("Plan"), {
      target: { value: PLAN_ID },
    });
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-10-17" },
    });
    fireEvent.change(screen.getByLabelText("Discount"), {
      target: { value: "10.50" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start membership" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith("memberships/", {
        member_id: LATER_MEMBER_ID,
        plan_id: PLAN_ID,
        start_date: "2026-10-17",
        discount_amount: "10.50",
      }),
    );
  });

  it("keeps the selected member when a stale search response arrives", async () => {
    const requests: Array<{
      search: string;
      pending: ReturnType<typeof deferred<MemberListPage>>;
    }> = [];
    vi.mocked(listMembers).mockImplementation(async (query = {}) => {
      const pending = deferred<MemberListPage>();
      requests.push({ search: query.search ?? "", pending });
      return pending.promise;
    });
    renderManager();
    await screen.findByRole("option", { name: "Monthly — 1500.00 THB" });

    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Ada" },
    });
    await waitFor(() => expect(requests).toHaveLength(1));
    requests[0]?.pending.resolve(memberPage([member()]));
    fireEvent.click(
      await screen.findByRole("button", { name: "MEM-000001 — Ada Lovelace" }),
    );

    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "G" },
    });
    await waitFor(() => expect(requests).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Grace" },
    });
    await waitFor(() => expect(requests).toHaveLength(3));
    requests[1]?.pending.resolve(
      memberPage([
        member({
          first_name: "Stale",
          last_name: "Result",
          member_number: "MEM-9",
        }),
      ]),
    );
    expect(
      screen.queryByRole("button", { name: /Stale Result/ }),
    ).not.toBeInTheDocument();
    requests[2]?.pending.resolve(
      memberPage([
        member({
          id: LATER_MEMBER_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000002",
        }),
      ]),
    );

    expect(
      await screen.findByRole("button", { name: "MEM-000002 — Grace Hopper" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Selected member: MEM-000001 — Ada Lovelace"),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Plan"), {
      target: { value: PLAN_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start membership" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        "memberships/",
        expect.objectContaining({ member_id: MEMBER_ID }),
      ),
    );
  });

  it("pages memberships, applies allowlisted filters, and resets onto one expiry bucket", async () => {
    const later = membership({
      id: LATER_MEMBERSHIP_ID,
      member_id: LATER_MEMBER_ID,
      plan_name: "Annual",
      status: "EXPIRED",
    });
    vi.mocked(listMemberships)
      .mockResolvedValueOnce(
        membershipPage([membership(), membership()], "cursor.next"),
      )
      .mockResolvedValueOnce(
        membershipPage([membership(), later], "cursor.next"),
      );
    renderManager();

    expect(screen.getByText("Loading memberships.")).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more memberships" }),
    );
    expect(await screen.findByText("Annual")).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: "Memberships" })).getAllByText(
        "Monthly",
      ),
    ).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "Load more memberships" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "ACTIVE" },
    });
    fireEvent.change(screen.getByLabelText("Filter by payment status"), {
      target: { value: "UNPAID" },
    });
    await waitFor(() =>
      expect(listMemberships).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ACTIVE",
        payment_status: "UNPAID",
      }),
    );
    expect(screen.queryByText("Annual")).not.toBeInTheDocument();

    vi.mocked(listMembershipExpiry).mockResolvedValue(
      membershipPage([
        membership({ id: LATER_MEMBERSHIP_ID, plan_name: "Due soon" }),
      ]),
    );
    fireEvent.change(screen.getByLabelText("Membership list"), {
      target: { value: "within_7_days" },
    });
    expect(screen.getByLabelText("Filter by status")).toBeDisabled();
    expect(screen.getByLabelText("Filter by payment status")).toBeDisabled();
    await waitFor(() =>
      expect(listMembershipExpiry).toHaveBeenLastCalledWith({
        bucket: "within_7_days",
        limit: 25,
      }),
    );
    expect(listMembershipExpiry).not.toHaveBeenCalledWith(
      expect.objectContaining({ status: expect.anything() }),
    );
    expect(await screen.findByText("Due soon")).toBeInTheDocument();
    expect(screen.queryByText("Annual")).not.toBeInTheDocument();
  });

  it("shows every active plan from the bounded catalog and a cap failure without a partial list", async () => {
    vi.mocked(listAllMembershipPlans).mockResolvedValue([
      plan(),
      plan({
        id: LATER_PLAN_ID,
        code: "YEAR",
        name: "Annual",
        price: "9000.00",
      }),
      plan({
        id: "88888888-8888-4888-8888-888888888888",
        name: "Old",
        is_active: false,
      }),
    ]);
    renderManager();

    expect(
      await screen.findByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Annual — 9000.00 THB" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Old/ }),
    ).not.toBeInTheDocument();
    const planCatalog = screen.getByRole("table", { name: "Membership plans" });
    expect(
      within(planCatalog).getByRole("cell", { name: "Old" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Archived" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Old (MONTH) plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Annual (YEAR) plan" }),
    ).toBeInTheDocument();
    expect(listMembershipPlans).not.toHaveBeenCalled();

    vi.mocked(listAllMembershipPlans).mockRejectedValue(
      new Error("The membership plan list is too large to load."),
    );
    fireEvent.change(screen.getByLabelText("Code"), {
      target: { value: "DAY" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Daily" },
    });
    fireEvent.change(screen.getByLabelText("Duration"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("Price"), {
      target: { value: "100.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));

    expect(
      await screen.findByText("The membership plan list is too large to load."),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen.queryByRole("table", { name: "Membership plans" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No membership plans.")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry loading plans" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Plan")).toBeDisabled();
    expect(
      screen.queryByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Annual — 9000.00 THB" }),
    ).not.toBeInTheDocument();
  });

  it("suspends, resumes, and cancels through the lifecycle endpoints", async () => {
    renderManager();
    await screen.findByRole("button", { name: "Suspend" });
    vi.mocked(listMemberships).mockResolvedValue(
      membershipPage([membership({ status: "SUSPENDED" })]),
    );
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `memberships/${MEMBERSHIP_ID}/suspend/`,
        {},
      ),
    );
    expect(
      vi.mocked(listMemberships).mock.calls.at(-1)?.[0],
    ).not.toHaveProperty("after");

    vi.mocked(operationMutation).mockClear();
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `memberships/${MEMBERSHIP_ID}/resume/`,
        {},
      ),
    );

    const prompt = vi
      .spyOn(window, "prompt")
      .mockReturnValue("  Member moved  ");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `memberships/${MEMBERSHIP_ID}/cancel/`,
        { reason: "Member moved" },
      ),
    );
    expect(prompt).toHaveBeenCalledWith("Cancellation reason");

    vi.mocked(operationMutation).mockClear();
    prompt.mockReturnValue("   ");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(operationMutation).not.toHaveBeenCalled();
    prompt.mockReturnValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(operationMutation).not.toHaveBeenCalled();
    prompt.mockRestore();
  });

  it("hides create and lifecycle actions without permission", async () => {
    renderManager({ canCancel: false, canCreate: false, canUpdate: false });

    expect(await screen.findByText("ACTIVE / UNPAID")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "New plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Membership plans" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Search members")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Suspend" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel" }),
    ).not.toBeInTheDocument();
    expect(listAllMembershipPlans).not.toHaveBeenCalled();
  });

  it("shows retryable membership errors without dropping the loaded page", async () => {
    vi.mocked(listMemberships)
      .mockResolvedValueOnce(membershipPage([membership()], "cursor.next"))
      .mockRejectedValueOnce(new Error("Membership page unavailable."));
    renderManager();
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more memberships" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Membership page unavailable.",
    );
    expect(
      within(screen.getByRole("table", { name: "Memberships" })).getByText(
        "Monthly",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Loading more memberships."),
    ).not.toBeInTheDocument();
    vi.mocked(listMemberships).mockResolvedValueOnce(
      membershipPage([
        membership({ id: LATER_MEMBERSHIP_ID, plan_name: "Annual" }),
      ]),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading memberships" }),
    );
    expect(await screen.findByText("Annual")).toBeInTheDocument();
  });

  it("loads the plan catalog for update-only staff and hides create forms", async () => {
    const pending = deferred<MembershipPlan[]>();
    vi.mocked(listAllMembershipPlans).mockReturnValue(pending.promise);
    renderManager({ canCreate: false, canUpdate: true });

    expect(screen.getByText("Loading membership plans.")).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Membership plans" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "New plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Start membership" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Search members")).not.toBeInTheDocument();
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);

    pending.resolve([
      plan(),
      plan({
        id: LATER_PLAN_ID,
        code: "YEAR",
        name: "Annual",
        duration_value: 12,
        duration_unit: "MONTH",
        price: "9000.00",
      }),
    ]);

    const planCatalog = await screen.findByRole("table", {
      name: "Membership plans",
    });
    expect(
      screen.queryByText("Loading membership plans."),
    ).not.toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "MONTH" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Monthly" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "1 Month" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "1500.00 THB" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "12 Months" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "9000.00 THB" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getAllByRole("cell", { name: "Active" }),
    ).toHaveLength(2);
    expect(
      screen.getByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Annual (YEAR) plan" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Plan")).not.toBeInTheDocument();
  });

  it("shows an empty plan catalog without treating it as a load failure", async () => {
    vi.mocked(listAllMembershipPlans).mockResolvedValue([]);
    renderManager({ canCreate: false, canUpdate: true });

    expect(await screen.findByText("No membership plans.")).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Membership plans" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "New plan" }),
    ).not.toBeInTheDocument();
  });

  it("hides archive actions when update permission is absent", async () => {
    vi.mocked(listAllMembershipPlans).mockResolvedValue([
      plan(),
      plan({
        id: LATER_PLAN_ID,
        code: "OLD",
        name: "Legacy",
        is_active: false,
        archived_at: "2026-09-01T00:00:00Z",
      }),
    ]);
    renderManager({ canUpdate: false });

    const planCatalog = await screen.findByRole("table", {
      name: "Membership plans",
    });
    expect(
      within(planCatalog).getByRole("cell", { name: "Legacy" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Archived" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Active" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Legacy (OLD) plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "New plan" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Legacy/ }),
    ).not.toBeInTheDocument();
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);
  });

  it("does not archive a plan when confirmation is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderManager({ canCreate: false, canUpdate: true });

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Archive Monthly (MONTH) plan",
      }),
    );

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith("Archive plan Monthly (MONTH)?");
    expect(operationMutation).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).toBeEnabled();
    expect(
      within(screen.getByRole("table", { name: "Membership plans" })).getByRole(
        "cell",
        { name: "Active" },
      ),
    ).toBeInTheDocument();
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);
  });

  it("merges an authoritative archived plan without reloading the catalog", async () => {
    vi.mocked(listAllMembershipPlans).mockResolvedValue([
      plan(),
      plan({
        id: LATER_PLAN_ID,
        code: "YEAR",
        name: "Annual",
        price: "9000.00",
      }),
    ]);
    vi.mocked(operationMutation).mockResolvedValue(
      plan({
        is_active: false,
        archived_at: "2026-10-17T12:00:00Z",
        price: "1499.00",
        updated_at: "2026-10-17T12:00:00Z",
      }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderManager();
    await screen.findByText("ACTIVE / UNPAID");
    const membershipLoads = vi.mocked(listMemberships).mock.calls.length;

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Archive Monthly (MONTH) plan",
      }),
    );

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `membership-plans/${PLAN_ID}/archive/`,
        {},
      ),
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith("Archive plan Monthly (MONTH)?");
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);
    expect(vi.mocked(listMemberships).mock.calls.length).toBe(membershipLoads);

    const planCatalog = screen.getByRole("table", { name: "Membership plans" });
    expect(
      within(planCatalog).getByRole("cell", { name: "1499.00 THB" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Archived" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).queryByRole("cell", { name: "1500.00 THB" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Annual (YEAR) plan" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Monthly — 1499.00 THB" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Annual — 9000.00 THB" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Restore|Delete/ }),
    ).not.toBeInTheDocument();
  });

  it("ignores a second archive click while the command is pending", async () => {
    const pending = deferred<MembershipPlan>();
    vi.mocked(operationMutation).mockReturnValue(pending.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderManager({ canCreate: false, canUpdate: true });

    const button = await screen.findByRole("button", {
      name: "Archive Monthly (MONTH) plan",
    });
    button.click();
    button.click();

    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(operationMutation).toHaveBeenCalledWith(
      `membership-plans/${PLAN_ID}/archive/`,
      {},
    );
    await waitFor(() => expect(button).toBeDisabled());
    pending.resolve(
      plan({
        is_active: false,
        archived_at: "2026-10-17T12:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Archive Monthly (MONTH) plan" }),
      ).not.toBeInTheDocument(),
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);
    expect(
      within(screen.getByRole("table", { name: "Membership plans" })).getByRole(
        "cell",
        { name: "Archived" },
      ),
    ).toBeInTheDocument();
  });

  it("keeps the active plan and shows an alert when archive fails", async () => {
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Plan archive was rejected."),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderManager();

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Archive Monthly (MONTH) plan",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Plan archive was rejected.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).toBeEnabled();
    expect(
      within(screen.getByRole("table", { name: "Membership plans" })).getByRole(
        "cell",
        { name: "Active" },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).toBeInTheDocument();
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(1);
  });

  it("retries a plan catalog failure without showing a partial catalog", async () => {
    vi.mocked(listAllMembershipPlans)
      .mockRejectedValueOnce(
        new Error("The membership plan list is too large to load."),
      )
      .mockResolvedValueOnce([
        plan(),
        plan({
          id: LATER_PLAN_ID,
          code: "YEAR",
          name: "Annual",
          price: "9000.00",
          is_active: false,
          archived_at: "2026-09-01T00:00:00Z",
        }),
      ]);
    renderManager({ canCreate: false, canUpdate: true });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The membership plan list is too large to load.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen.queryByRole("table", { name: "Membership plans" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No membership plans.")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("cell", { name: "MONTH" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "New plan" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Search members")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading plans" }),
    );

    const planCatalog = await screen.findByRole("table", {
      name: "Membership plans",
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "MONTH" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "YEAR" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Active" }),
    ).toBeInTheDocument();
    expect(
      within(planCatalog).getByRole("cell", { name: "Archived" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Monthly (MONTH) plan" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Annual (YEAR) plan" }),
    ).not.toBeInTheDocument();
    expect(listAllMembershipPlans).toHaveBeenCalledTimes(2);
  });
});

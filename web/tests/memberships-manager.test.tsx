import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { operationGet, operationMutation } from "@/features/operations/client";
import { MembershipsManager } from "@/features/operations/memberships-manager";
import type { Member, Membership, MembershipPlan } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
  operationMutation: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const PLAN_ID = "22222222-2222-4222-8222-222222222222";
const MEMBERSHIP_ID = "33333333-3333-4333-8333-333333333333";

function member(): Member {
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
  };
}

function plan(): MembershipPlan {
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

function mockLists(row: Membership = membership()) {
  vi.mocked(operationGet).mockImplementation(async (path: string) => {
    if (path === "members/") return [member()];
    if (path === "membership-plans/") return [plan()];
    if (path === "memberships/") return [row];
    throw new Error(path);
  });
}

function renderManager(row: Membership = membership()) {
  mockLists(row);
  render(
    <MembershipsManager
      canCancel
      canCreate
      canUpdate
      currency="THB"
      defaultDate="2026-10-17"
    />,
  );
}

describe("Memberships manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(operationMutation).mockResolvedValue({});
    mockLists();
  });

  it("renders published member, plan, and membership fields", async () => {
    renderManager();

    expect(
      await screen.findByRole("option", { name: "MEM-000001 — Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("ada lovelace")).not.toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Monthly — 1500.00 THB" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
    expect(screen.getByText("2026-01-31 – 2026-02-27")).toBeInTheDocument();
    expect(screen.getByText("1399.50 THB")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE / UNPAID")).toBeInTheDocument();
    expect(screen.getByLabelText("Currency")).toHaveValue("THB");
    expect(screen.getByLabelText("Currency")).not.toHaveAttribute("name");
  });

  it("creates a plan without sending currency", async () => {
    renderManager();
    await screen.findByRole("option", { name: "Monthly — 1500.00 THB" });

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
  });

  it("creates a membership with server-allowed fields", async () => {
    renderManager();
    await screen.findByRole("option", { name: "MEM-000001 — Ada Lovelace" });

    fireEvent.change(screen.getByLabelText("Member"), {
      target: { value: MEMBER_ID },
    });
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
        member_id: MEMBER_ID,
        plan_id: PLAN_ID,
        start_date: "2026-10-17",
        discount_amount: "10.50",
      }),
    );
  });

  it("suspends and resumes through the lifecycle endpoints", async () => {
    renderManager();
    fireEvent.click(await screen.findByRole("button", { name: "Suspend" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `memberships/${MEMBERSHIP_ID}/suspend/`,
        {},
      ),
    );

    vi.mocked(operationMutation).mockClear();
    renderManager(membership({ status: "SUSPENDED" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `memberships/${MEMBERSHIP_ID}/resume/`,
        {},
      ),
    );
  });

  it("cancels with the trimmed staff reason and aborts a blank reason", async () => {
    const prompt = vi
      .spyOn(window, "prompt")
      .mockReturnValue("  Member moved  ");
    renderManager();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
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
});

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { operationGet, operationMutation } from "@/features/operations/client";
import { MemberDetail } from "@/features/operations/member-detail";
import type { Member, Membership, MemberSummary } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
  operationMutation: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const MEMBERSHIP_ID = "22222222-2222-4222-8222-222222222222";

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
    notes: "Morning visits",
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

function membership(overrides: Partial<Membership> = {}): Membership {
  return {
    id: MEMBERSHIP_ID,
    member_id: MEMBER_ID,
    plan_id: "33333333-3333-4333-8333-333333333333",
    plan_code: "MONTH",
    plan_name: "Monthly",
    duration_value: 1,
    duration_unit: "MONTH",
    start_date: "2026-10-01",
    expiry_date: "2026-10-31",
    list_price: "1500.00",
    discount_amount: "0.00",
    final_amount: "1500.00",
    currency: "THB",
    payment_status: "PARTIAL",
    status: "ACTIVE",
    note: "",
    cancelled_at: null,
    cancelled_by_membership_id: null,
    cancellation_reason: "",
    created_by_membership_id: "44444444-4444-4444-8444-444444444444",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function summary(overrides: Partial<MemberSummary> = {}): MemberSummary {
  return {
    member: member(),
    current_membership: membership(),
    days_remaining: 15,
    is_pt_client: false,
    membership_history: [membership()],
    membership_history_has_more: false,
    payment_summary: {
      available: true,
      currency: "THB",
      total_charged: "40.00",
      total_refunded: "10.00",
      net_paid: "30.00",
      outstanding_membership_balance: "70.00",
    },
    check_in_summary: { available: false },
    ...overrides,
  };
}

describe("Member detail summary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading status before the summary arrives", () => {
    vi.mocked(operationGet).mockReturnValue(new Promise(() => undefined));

    render(<MemberDetail canUpdate={false} memberId={MEMBER_ID} />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading member summary.",
    );
  });

  it("shows the membership, payment summary, and unavailable check-ins", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({ is_pt_client: true, membership_history_has_more: true }),
    );

    render(<MemberDetail canUpdate={false} memberId={MEMBER_ID} />);

    expect(
      await screen.findByRole("heading", { name: "Current membership" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Monthly: 2026-10-01 – 2026-10-31 (PARTIAL)"),
    ).toBeInTheDocument();
    expect(screen.getByText(/15 days remaining/)).toBeInTheDocument();
    expect(screen.getByText(/PT client/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Payment summary" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Total charged").nextElementSibling,
    ).toHaveTextContent("40.00 THB");
    expect(
      screen.getByText("Total refunded").nextElementSibling,
    ).toHaveTextContent("10.00 THB");
    expect(screen.getByText("Net paid").nextElementSibling).toHaveTextContent(
      "30.00 THB",
    );
    expect(
      screen.getByText("Outstanding membership balance").nextElementSibling,
    ).toHaveTextContent("70.00 THB");
    expect(
      screen.queryByText("do-not-leak-description"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Older memberships are not shown/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Check-in summary is not available yet."),
    ).toBeInTheDocument();
    expect(operationGet).toHaveBeenCalledWith(`members/${MEMBER_ID}/summary/`);
  });

  it("shows empty membership, payment, and history states", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({
        current_membership: null,
        days_remaining: null,
        membership_history: [],
        membership_history_has_more: false,
        payment_summary: {
          available: true,
          currency: "THB",
          total_charged: "0.00",
          total_refunded: "0.00",
          net_paid: "0.00",
          outstanding_membership_balance: "0.00",
        },
      }),
    );

    render(<MemberDetail canUpdate memberId={MEMBER_ID} />);

    expect(
      await screen.findByText("No current membership."),
    ).toBeInTheDocument();
    expect(screen.getByText("No finalized payments.")).toBeInTheDocument();
    expect(screen.getByText("No membership history.")).toBeInTheDocument();
    expect(screen.queryByText("PT client")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Payment summary" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save changes" }),
    ).toBeInTheDocument();
  });

  it("exposes a load failure as an alert", async () => {
    vi.mocked(operationGet).mockRejectedValue(
      new Error("Unable to load member."),
    );

    render(<MemberDetail canUpdate={false} memberId={MEMBER_ID} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to load member.",
    );
    expect(
      screen.queryByRole("heading", { name: "Payment summary" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the summary visible when saving fails", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Unable to update member."),
    );

    render(<MemberDetail canUpdate memberId={MEMBER_ID} />);
    expect(
      await screen.findByRole("button", { name: "Save changes" }),
    ).toBeInTheDocument();
    screen.getByRole("button", { name: "Save changes" }).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to update member.",
    );
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    await waitFor(() => expect(operationMutation).toHaveBeenCalled());
  });
});

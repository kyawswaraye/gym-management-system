import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  listMemberPayments,
  operationGet,
  operationMutation,
} from "@/features/operations/client";
import { MemberDetail } from "@/features/operations/member-detail";
import type {
  Member,
  MemberPaymentHistoryItem,
  Membership,
  MemberSummary,
} from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
  operationMutation: vi.fn(),
  listMemberPayments: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_MEMBER_ID = "66666666-6666-4666-8666-666666666666";
const MEMBERSHIP_ID = "22222222-2222-4222-8222-222222222222";
const PAYMENT_ID = "55555555-5555-4555-8555-555555555555";
const OLDER_PAYMENT_ID = "77777777-7777-4777-8777-777777777777";

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

function payment(
  overrides: Partial<MemberPaymentHistoryItem> = {},
): MemberPaymentHistoryItem {
  return {
    id: PAYMENT_ID,
    membership_id: null,
    original_payment_id: null,
    entry_type: "CHARGE",
    purpose: "MEMBERSHIP",
    description: "May dues",
    amount: "40.10",
    currency: "THB",
    method: "CASH",
    business_date: "2024-06-03",
    received_at: "2024-06-03T08:00:00Z",
    status: "POSTED",
    receipt_number: "RCPT-100",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("Member detail summary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading status before the summary arrives", () => {
    vi.mocked(operationGet).mockReturnValue(new Promise(() => undefined));

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading member summary.",
    );
  });

  it("shows the membership, payment summary, and unavailable check-ins", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({ is_pt_client: true, membership_history_has_more: true }),
    );

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: "Current membership" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Status").nextElementSibling).toHaveTextContent(
      "ACTIVE",
    );
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
    expect(listMemberPayments).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: "Payment history" }),
    ).not.toBeInTheDocument();
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

    render(
      <MemberDetail
        canArchive={false}
        canUpdate
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );

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

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );

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

    render(
      <MemberDetail
        canArchive={false}
        canUpdate
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );
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

  it("loads payment history and older pages without duplicating a cursor", async () => {
    const older = deferred<Awaited<ReturnType<typeof listMemberPayments>>>();
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(listMemberPayments).mockImplementation(
      async (_memberId, after) => {
        if (!after) {
          return {
            data: [payment()],
            meta: { has_more: true, next_after: "cursor-1" },
          };
        }
        return older.promise;
      },
    );

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        memberId={MEMBER_ID}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: "Payment history" }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/May dues/)).toHaveTextContent("POSTED");
    expect(screen.getByText(/Receipt RCPT-100/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Payment summary" }),
    ).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Load older payments" });
    button.click();
    button.click();

    expect(listMemberPayments).toHaveBeenCalledTimes(2);
    expect(listMemberPayments).toHaveBeenLastCalledWith(MEMBER_ID, "cursor-1");
    older.resolve({
      data: [
        payment(),
        payment({
          id: OLDER_PAYMENT_ID,
          description: "April dues",
          receipt_number: null,
          status: "VOID",
        }),
      ],
      meta: { has_more: false, next_after: null },
    });

    expect(await screen.findByText(/April dues/)).toHaveTextContent("VOID");
    expect(screen.getAllByText(/May dues/)).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "Load older payments" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty payment history without hiding the summary", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(listMemberPayments).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        memberId={MEMBER_ID}
      />,
    );

    expect(await screen.findByText("No payments.")).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load older payments" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the summary usable when payment history fails", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(listMemberPayments).mockRejectedValue(
      new Error("Unable to load payment history."),
    );

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        memberId={MEMBER_ID}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to load payment history.",
    );
    expect(screen.getByText("Net paid")).toBeInTheDocument();
    expect(screen.queryByText("No payments.")).not.toBeInTheDocument();
  });

  it("ignores a stale older-page response after the member changes", async () => {
    const older = deferred<Awaited<ReturnType<typeof listMemberPayments>>>();
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(listMemberPayments).mockImplementation(
      async (memberId, after) => {
        if (after) return older.promise;
        if (memberId === OTHER_MEMBER_ID) {
          return {
            data: [payment({ id: OTHER_MEMBER_ID, description: "Other dues" })],
            meta: { has_more: false, next_after: null },
          };
        }
        return {
          data: [payment()],
          meta: { has_more: true, next_after: "cursor-1" },
        };
      },
    );
    const view = render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        memberId={MEMBER_ID}
      />,
    );
    const button = await screen.findByRole("button", {
      name: "Load older payments",
    });
    button.click();
    view.rerender(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        memberId={OTHER_MEMBER_ID}
      />,
    );

    expect(await screen.findByText(/Other dues/)).toBeInTheDocument();
    older.resolve({
      data: [payment({ id: OLDER_PAYMENT_ID, description: "Stale dues" })],
      meta: { has_more: true, next_after: "cursor-1" },
    });
    await waitFor(() =>
      expect(screen.queryByText(/Stale dues/)).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Other dues/)).toBeInTheDocument();
  });
});

describe("Member archive", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "prompt").mockImplementation(() => {
      throw new Error("browser prompt");
    });
  });

  afterEach(() => {
    vi.mocked(window.prompt).mockRestore();
    if (vi.isMockFunction(window.confirm)) {
      vi.mocked(window.confirm).mockRestore();
    }
  });

  function renderMember(canArchive: boolean) {
    return render(
      <MemberDetail
        canArchive={canArchive}
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );
  }

  it("hides archive without permission and when the member is already archived", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    const view = renderMember(false);

    expect(await screen.findByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
    view.unmount();

    vi.mocked(operationGet).mockResolvedValue(
      summary({
        member: member({
          status: "ARCHIVED",
          archived_at: "2026-10-17T00:00:00Z",
        }),
      }),
    );
    renderMember(true);

    expect(await screen.findByText("ARCHIVED")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
  });

  it("does not send a request when archive is cancelled", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderMember(true);

    (await screen.findByRole("button", { name: "Archive member" })).click();

    expect(confirm).toHaveBeenCalledWith(
      "Archive member Ada Lovelace (MEM-000001)?",
    );
    expect(operationMutation).not.toHaveBeenCalled();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive member" }),
    ).toBeInTheDocument();
  });

  it("archives after confirmation and refreshes the summary", async () => {
    vi.mocked(operationGet)
      .mockResolvedValueOnce(summary())
      .mockResolvedValueOnce(
        summary({
          member: member({
            status: "ARCHIVED",
            archived_at: "2026-10-17T00:00:00Z",
            authz_version: 2,
          }),
        }),
      );
    vi.mocked(operationMutation).mockResolvedValue(
      member({
        status: "ARCHIVED",
        archived_at: "2026-10-17T00:00:00Z",
        authz_version: 2,
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderMember(true);

    (await screen.findByRole("button", { name: "Archive member" })).click();

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `members/${MEMBER_ID}/archive/`,
        {},
      ),
    );
    expect(operationGet).toHaveBeenNthCalledWith(
      2,
      `members/${MEMBER_ID}/summary/`,
    );
    expect(await screen.findByText("ARCHIVED")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
  });

  it("ignores a second click while archive is pending", async () => {
    const pending = deferred<Member>();
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(operationMutation).mockReturnValue(pending.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderMember(true);

    const button = await screen.findByRole("button", {
      name: "Archive member",
    });
    button.click();
    button.click();

    expect(operationMutation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(button).toBeDisabled());
    pending.resolve(
      member({
        status: "ARCHIVED",
        archived_at: "2026-10-17T00:00:00Z",
      }),
    );
    await waitFor(() => expect(operationGet).toHaveBeenCalledTimes(2));
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("keeps the summary and shows an alert when archive fails", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Archive was rejected."),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderMember(true);

    (await screen.findByRole("button", { name: "Archive member" })).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Archive was rejected.",
    );
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive member" }),
    ).toBeEnabled();
    expect(operationGet).toHaveBeenCalledTimes(1);
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("keeps the archived member when summary refresh fails", async () => {
    vi.mocked(operationGet)
      .mockResolvedValueOnce(summary())
      .mockRejectedValueOnce(new Error("Summary refresh failed."));
    vi.mocked(operationMutation).mockResolvedValue(
      member({
        status: "ARCHIVED",
        archived_at: "2026-10-17T00:00:00Z",
        authz_version: 2,
        is_pt_client: true,
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderMember(true);

    (await screen.findByRole("button", { name: "Archive member" })).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Summary refresh failed.",
    );
    expect(screen.getByText("ARCHIVED")).toBeInTheDocument();
    expect(screen.getByText(/PT client/)).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(operationGet).toHaveBeenNthCalledWith(
      2,
      `members/${MEMBER_ID}/summary/`,
    );
  });
});

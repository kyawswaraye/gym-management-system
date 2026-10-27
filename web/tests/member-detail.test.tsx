import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listReceipts } from "@/features/finance/client";
import {
  getFileDownloadUrl,
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
  getFileDownloadUrl: vi.fn(),
}));

vi.mock("@/features/finance/client", () => ({
  listReceipts: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_MEMBER_ID = "66666666-6666-4666-8666-666666666666";
const PHOTO_ID = "88888888-8888-4888-8888-888888888888";
const MEMBERSHIP_ID = "22222222-2222-4222-8222-222222222222";
const PAYMENT_ID = "55555555-5555-4555-8555-555555555555";
const RECEIPT_ID = "99999999-9999-4999-8999-999999999999";
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
    check_in_summary: {
      available: true,
      currently_checked_in: false,
      open_check_in_id: null,
      last_check_in_at: null,
      last_check_out_at: null,
    },
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

function renderDetail(
  props: Partial<{
    canArchive: boolean;
    canUpdate: boolean;
    canViewPayments: boolean;
    canViewReceipts: boolean;
    canViewPhoto: boolean;
    memberId: string;
  }> = {},
) {
  return render(
    <MemberDetail
      canArchive={props.canArchive ?? false}
      canUpdate={props.canUpdate ?? false}
      canViewPayments={props.canViewPayments ?? false}
      canViewReceipts={props.canViewReceipts ?? false}
      canViewPhoto={props.canViewPhoto ?? false}
      memberId={props.memberId ?? MEMBER_ID}
    />,
  );
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

  it("shows the membership, payment summary, and check-in summary", async () => {
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
      screen.getByText("Monthly: 01-10-2026 – 31-10-2026 (PARTIAL)"),
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
    expect(screen.getByText("Not checked in")).toBeInTheDocument();
    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
    expect(operationGet).toHaveBeenCalledWith(`members/${MEMBER_ID}/summary/`);
    expect(listMemberPayments).not.toHaveBeenCalled();
    expect(listReceipts).not.toHaveBeenCalled();
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
      screen.getByRole("button", { name: "Edit profile" }),
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
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit profile" }),
    );
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
    expect(listReceipts).not.toHaveBeenCalled();
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
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
  });

  it("prints an issued history receipt only when receipt access can match it", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    vi.mocked(listMemberPayments).mockResolvedValue({
      data: [
        payment(),
        payment({
          id: OLDER_PAYMENT_ID,
          description: "April dues",
          receipt_number: "RCPT-OLD",
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listReceipts).mockResolvedValue({
      data: [
        {
          id: RECEIPT_ID,
          payment_id: PAYMENT_ID,
          status: "ISSUED",
        },
        {
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          payment_id: OLDER_PAYMENT_ID,
          status: "VOID",
        },
      ],
      meta: { has_more: false, next_after: null },
    } as never);

    render(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments
        canViewReceipts
        memberId={MEMBER_ID}
      />,
    );

    expect(await screen.findByText(/Receipt RCPT-100/)).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "Print receipt" });
    expect(link).toHaveAttribute("href", `/receipts/${RECEIPT_ID}/print`);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getAllByRole("link", { name: "Print receipt" })).toHaveLength(
      1,
    );
    expect(listReceipts).toHaveBeenCalledWith({
      member: MEMBER_ID,
      status: "ISSUED",
      limit: "100",
    });
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
  });

  function confirmArchive() {
    const dialog = screen.getByRole("dialog", {
      name: "Archive Ada Lovelace (MEM-000001)?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
    return dialog;
  }

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
    expect(screen.getAllByText("Email").length).toBeGreaterThan(0);
    expect(screen.getByText("ada@example.test")).toBeInTheDocument();
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
    renderMember(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Archive member" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Archive Ada Lovelace (MEM-000001)?",
    });
    expect(dialog).toHaveTextContent("not deleted");
    expect(dialog).toHaveTextContent("history remain");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(operationMutation).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", {
        name: "Archive Ada Lovelace (MEM-000001)?",
      }),
    ).not.toBeInTheDocument();
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
    renderMember(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Archive member" }),
    );
    confirmArchive();

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
    renderMember(true);

    const button = await screen.findByRole("button", {
      name: "Archive member",
    });
    fireEvent.click(button);
    const confirm = within(
      screen.getByRole("dialog", {
        name: "Archive Ada Lovelace (MEM-000001)?",
      }),
    ).getByRole("button", { name: "Archive" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

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
    renderMember(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Archive member" }),
    );
    confirmArchive();

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
    renderMember(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Archive member" }),
    );
    confirmArchive();

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

describe("Member restore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function archivedSummary() {
    return summary({
      member: member({
        status: "ARCHIVED",
        archived_at: "2026-10-17T00:00:00Z",
        authz_version: 2,
      }),
    });
  }

  function confirmRestore() {
    const dialog = screen.getByRole("dialog", {
      name: "Restore Ada Lovelace (MEM-000001)?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    return dialog;
  }

  function renderArchived(canRestore: boolean, canArchive = false) {
    return render(
      <MemberDetail
        canArchive={canArchive}
        canRestore={canRestore}
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );
  }

  it("shows restore only for an archived member when the caller can restore", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    const active = render(
      <MemberDetail
        canArchive
        canRestore
        canUpdate={false}
        canViewPayments={false}
        memberId={MEMBER_ID}
      />,
    );

    expect(await screen.findByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Restore member" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive member" }),
    ).toBeInTheDocument();
    active.unmount();

    vi.mocked(operationGet).mockResolvedValue(archivedSummary());
    const hidden = renderArchived(false, true);
    expect(await screen.findByText("ARCHIVED")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Restore member" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
    hidden.unmount();

    vi.mocked(operationGet).mockResolvedValue(archivedSummary());
    renderArchived(true, true);
    expect(
      await screen.findByRole("button", { name: "Restore member" }),
    ).toHaveClass("button-success");
    expect(
      screen.queryByRole("button", { name: "Archive member" }),
    ).not.toBeInTheDocument();
  });

  it("does not send a request when restore is cancelled", async () => {
    vi.mocked(operationGet).mockResolvedValue(archivedSummary());
    renderArchived(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Restore member" }),
    );
    expect(
      screen.getByRole("dialog", {
        name: "Restore Ada Lovelace (MEM-000001)?",
      }),
    ).toHaveTextContent("not deleted");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(operationMutation).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", {
        name: "Restore Ada Lovelace (MEM-000001)?",
      }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("ARCHIVED")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore member" }),
    ).toBeInTheDocument();
  });

  it("restores after confirmation, refreshes the member, and shows ACTIVE", async () => {
    vi.mocked(operationGet)
      .mockResolvedValueOnce(archivedSummary())
      .mockResolvedValueOnce(
        summary({
          member: member({
            status: "ACTIVE",
            archived_at: null,
            authz_version: 3,
          }),
        }),
      );
    vi.mocked(operationMutation).mockResolvedValue(
      member({
        status: "ACTIVE",
        archived_at: null,
        authz_version: 3,
      }),
    );
    renderArchived(true, true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Restore member" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Restore Ada Lovelace (MEM-000001)?",
    });
    expect(within(dialog).getByRole("button", { name: "Restore" })).toHaveClass(
      "button-success",
    );
    confirmRestore();

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `members/${MEMBER_ID}/restore/`,
        {},
      ),
    );
    expect(operationGet).toHaveBeenNthCalledWith(
      2,
      `members/${MEMBER_ID}/summary/`,
    );
    expect(await screen.findByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Restore member" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive member" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
  });

  it("keeps the archived member and shows an alert when restore fails", async () => {
    vi.mocked(operationGet).mockResolvedValue(archivedSummary());
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Restore was rejected."),
    );
    renderArchived(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "Restore member" }),
    );
    confirmRestore();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Restore was rejected.",
    );
    expect(screen.getByText("ARCHIVED")).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore member" }),
    ).toBeEnabled();
    expect(operationGet).toHaveBeenCalledTimes(1);
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });
});

describe("Member profile editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(operationGet).mockResolvedValue(summary());
  });

  it("keeps profile editing available without file permissions", async () => {
    renderDetail({ canUpdate: true });

    expect(await screen.findByText("Morning visits")).toBeInTheDocument();
    expect(screen.getByText("555")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));

    expect(await screen.findByLabelText("Notes")).toHaveValue("Morning visits");
    expect(screen.getByLabelText("First name")).toHaveValue("Ada");
    expect(screen.getByLabelText("First name")).toBeEnabled();
    expect(screen.getByText("Status").nextElementSibling).toHaveTextContent(
      "ACTIVE",
    );
    expect(
      screen.getByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Upload profile photo"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Remove profile photo" }),
    ).not.toBeInTheDocument();
    expect(getFileDownloadUrl).not.toHaveBeenCalled();
  });

  it("shows profile details without members.update", async () => {
    renderDetail();

    expect(await screen.findByText("Morning visits")).toBeInTheDocument();
    expect(screen.getByText("555")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit profile" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save changes" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("First name")).not.toBeInTheDocument();
  });

  it("saves the writable profile and blank nullable fields", async () => {
    renderDetail({ canUpdate: true });
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit profile" }),
    );
    expect(await screen.findByLabelText("Phone")).toHaveValue("555");

    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "  Grace  " },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "  Hopper  " },
    });
    fireEvent.change(screen.getByLabelText("Phone"), {
      target: { value: "  " },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "  " },
    });
    fireEvent.change(screen.getByLabelText("Gender"), {
      target: { value: "OTHER" },
    });
    fireEvent.change(screen.getByLabelText("Date of birth"), {
      target: { value: "1980-01-02" },
    });
    fireEvent.change(screen.getByLabelText("Address line 1"), {
      target: { value: "  9  Oak  " },
    });
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "Yangon Region" },
    });
    fireEvent.change(screen.getByLabelText("City"), {
      target: { value: "Yangon" },
    });
    fireEvent.change(screen.getByLabelText("Country code"), {
      target: { value: " th " },
    });
    fireEvent.change(screen.getByLabelText("Join date"), {
      target: { value: "2026-03-01" },
    });
    fireEvent.change(screen.getByLabelText("Emergency contact name"), {
      target: { value: "  Sam  " },
    });
    fireEvent.change(screen.getByLabelText("Emergency contact phone"), {
      target: { value: "  " },
    });
    fireEvent.change(screen.getByLabelText("Notes"), {
      target: { value: "  Updated  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `members/${MEMBER_ID}/`,
        {
          first_name: "Grace",
          last_name: "Hopper",
          phone: null,
          email: null,
          gender: "OTHER",
          date_of_birth: "1980-01-02",
          address: {
            address_line1: "9 Oak",
            address_line2: "",
            city: "Yangon",
            region: "Yangon Region",
            postal_code: "",
            country_code: "TH",
          },
          join_date: "2026-03-01",
          emergency_contact_name: "Sam",
          emergency_contact_phone: "",
          notes: "Updated",
        },
        "PATCH",
      ),
    );
    const payload = vi.mocked(operationMutation).mock.calls[0]?.[1] as Record<
      string,
      unknown
    >;
    expect(payload).not.toHaveProperty("member_number");
    expect(payload).not.toHaveProperty("profile_photo_asset_id");
    expect(payload).not.toHaveProperty("status");
    expect(payload).not.toHaveProperty("gym_id");
    expect(payload).not.toHaveProperty("id");
  });

  it("keeps the summary and edited values when saving fails", async () => {
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Unable to update member."),
    );
    renderDetail({ canUpdate: true });
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit profile" }),
    );
    const firstName = await screen.findByLabelText("First name");
    fireEvent.change(firstName, { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to update member.",
    );
    expect(screen.getByLabelText("First name")).toHaveValue("Grace");
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("replaces edited fields with the reloaded profile", async () => {
    vi.mocked(operationGet)
      .mockResolvedValueOnce(summary())
      .mockResolvedValueOnce(
        summary({
          member: member({
            first_name: "Pat",
            updated_at: "2026-03-03T00:00:00Z",
          }),
        }),
      );
    vi.mocked(operationMutation).mockResolvedValue(
      member({ first_name: "Pat" }),
    );
    renderDetail({ canUpdate: true });
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit profile" }),
    );
    const firstName = await screen.findByLabelText("First name");
    fireEvent.change(firstName, { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Edit profile" }),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
    await waitFor(() =>
      expect(screen.getByLabelText("First name")).toHaveValue("Pat"),
    );
    expect(screen.queryByDisplayValue("Ada")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Grace")).not.toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
  });
});

describe("Member profile photo", () => {
  const signedUrl =
    "https://files.example.com/gym-private/photo?X-Amz-Signature=sig-do-not-leak";

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getFileDownloadUrl).mockResolvedValue({
      url: signedUrl,
      contentType: "image/jpeg",
    });
  });

  function expectPhotoControlsAbsent() {
    expect(
      screen.queryByLabelText("Upload profile photo"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Replace profile photo"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Remove profile photo" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry loading profile photo" }),
    ).not.toBeInTheDocument();
  }

  it("loads a fresh photo without rendering the signed URL as text", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({
        member: member({ profile_photo_asset_id: PHOTO_ID }),
      }),
    );
    renderDetail({ canViewPhoto: true, canUpdate: true });

    const image = await screen.findByRole("img", {
      name: "Profile photo of Ada Lovelace",
    });
    expect(image).toHaveAttribute("src", signedUrl);
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sig-do-not-leak");
    expect(document.body.textContent).not.toContain("sha256");
    expect(getFileDownloadUrl).toHaveBeenCalledWith(PHOTO_ID);
    fireEvent.error(image);
    expect(
      await screen.findByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: "Profile photo of Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expectPhotoControlsAbsent();
  });

  it("shows an empty profile photo when the member has not uploaded one", async () => {
    vi.mocked(operationGet).mockResolvedValue(summary());
    renderDetail({ canViewPhoto: true });

    expect(
      await screen.findByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(getFileDownloadUrl).not.toHaveBeenCalled();
    expectPhotoControlsAbsent();
  });

  it("ignores a stale photo response after the member changes", async () => {
    const pending = deferred<{ url: string; contentType: "image/jpeg" }>();
    vi.mocked(getFileDownloadUrl).mockReturnValue(pending.promise);
    vi.mocked(operationGet).mockImplementation(async (path: string) => {
      if (path.includes(OTHER_MEMBER_ID)) {
        return summary({
          member: member({
            id: OTHER_MEMBER_ID,
            first_name: "Grace",
            last_name: "Hopper",
            profile_photo_asset_id: null,
          }),
        });
      }
      return summary({
        member: member({ profile_photo_asset_id: PHOTO_ID }),
      });
    });
    const view = renderDetail({ canViewPhoto: true, memberId: MEMBER_ID });
    expect(
      await screen.findByText("Loading profile photo."),
    ).toBeInTheDocument();

    view.rerender(
      <MemberDetail
        canArchive={false}
        canUpdate={false}
        canViewPayments={false}
        canViewPhoto
        memberId={OTHER_MEMBER_ID}
      />,
    );
    pending.resolve({
      url: "https://files.example.com/stale?X-Amz-Signature=stale-secret",
      contentType: "image/jpeg",
    });

    expect(
      await screen.findByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: "Profile photo of Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("stale-secret");
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
    expectPhotoControlsAbsent();
  });

  it("shows an empty profile photo when the uploaded photo cannot be loaded", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({ member: member({ profile_photo_asset_id: PHOTO_ID }) }),
    );
    vi.mocked(getFileDownloadUrl).mockRejectedValue(
      new Error("Unable to load the profile photo."),
    );
    renderDetail({ canViewPhoto: true });

    expect(
      await screen.findByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(screen.getByText("Total charged")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expectPhotoControlsAbsent();
  });

  it("does not fetch a photo without file view permission", async () => {
    vi.mocked(operationGet).mockResolvedValue(
      summary({ member: member({ profile_photo_asset_id: PHOTO_ID }) }),
    );
    renderDetail({ canViewPhoto: false });

    expect(
      await screen.findByRole("img", { name: "No profile photo." }),
    ).toBeInTheDocument();
    expect(getFileDownloadUrl).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("sig-do-not-leak");
    expect(document.body.textContent).not.toContain(PHOTO_ID);
    expectPhotoControlsAbsent();
  });
});

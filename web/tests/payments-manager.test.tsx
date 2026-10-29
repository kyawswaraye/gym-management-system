import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approvePayment,
  getPayment,
  getReceiptPrintContext,
  listCashSessions,
  listPayments,
  listReceipts,
  refundPayment,
  submitPayment,
  voidPayment,
} from "@/features/finance/client";
import { PaymentsManager } from "@/features/finance/payments-manager";
import {
  getGymProfile,
  listMembers,
  listMemberships,
  OperationError,
  operationGet,
} from "@/features/operations/client";
import type {
  CashSession,
  GymProfile,
  Member,
  Payment,
  Receipt,
  ReceiptPrintContext,
} from "@/types/api";

function finishApproval() {
  fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
  fireEvent.click(screen.getByRole("button", { name: "Record this payment" }));
}

vi.mock("@/features/finance/client", () => ({
  approvePayment: vi.fn(),
  cancelPayment: vi.fn(),
  createPayment: vi.fn(),
  downloadReceiptDocument: vi.fn(),
  getPayment: vi.fn(),
  getReceiptPrintContext: vi.fn(),
  listCashSessions: vi.fn(),
  listPayments: vi.fn(),
  listReceipts: vi.fn(),
  refundPayment: vi.fn(),
  rejectPayment: vi.fn(),
  submitPayment: vi.fn(),
  updatePayment: vi.fn(),
  voidPayment: vi.fn(),
  voidReceipt: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {
    fields: Record<string, string>;
    code: string;
    constructor(
      message: string,
      fields: Record<string, string> = {},
      code = "",
    ) {
      super(message);
      this.fields = fields;
      this.code = code;
    }
  },
  getGymProfile: vi.fn(),
  listMembers: vi.fn(),
  listMemberships: vi.fn(),
  operationGet: vi.fn(),
}));

const PAYMENT_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";
const RECEIPT_ID = "66666666-6666-4666-8666-666666666666";
const MEMBER_ID = "77777777-7777-4777-8777-777777777777";
const PRINT_HREF = `/receipts/${RECEIPT_ID}/print?autoPrint=1`;

function payment(overrides: Partial<Payment> = {}): Payment {
  return {
    id: PAYMENT_ID,
    member_id: null,
    membership_id: null,
    pt_sale_id: null,
    original_payment_id: null,
    reversal_payment_id: null,
    entry_type: "CHARGE",
    purpose: "OTHER",
    description: "Locker key",
    amount: "25.00",
    currency: "THB",
    method: "CASH",
    business_date: "2026-10-18",
    received_at: "2026-10-18T02:00:00Z",
    reference_number: "",
    status: "PENDING_APPROVAL",
    rejection_reason: "",
    cancellation_reason: "",
    void_reason: "",
    submitted_at: "2026-10-18T02:00:00Z",
    submitted_by_membership_id: "33333333-3333-4333-8333-333333333333",
    approved_at: null,
    approved_by_membership_id: null,
    rejected_at: null,
    rejected_by_membership_id: null,
    cancelled_at: null,
    cancelled_by_membership_id: null,
    posted_at: null,
    posted_by_membership_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    cash_session_id: null,
    cash_transaction_id: null,
    retail_sale_id: null,
    created_by_membership_id: "44444444-4444-4444-8444-444444444444",
    updated_by_membership_id: "44444444-4444-4444-8444-444444444444",
    created_at: "2026-10-18T02:00:00Z",
    updated_at: "2026-10-18T02:00:00Z",
    ...overrides,
  };
}

function till(): CashSession {
  return {
    id: SESSION_ID,
    business_date: "2026-10-18",
    register_code: "FRONT",
    currency: "THB",
    opening_cash: "0.00",
    cash_sales: "0.00",
    cash_expenses: "0.00",
    manual_net: "0.00",
    expected_closing: "0.00",
    actual_closing: null,
    difference: null,
    difference_reason: "",
    status: "OPEN",
    opened_by_membership_id: "55555555-5555-4555-8555-555555555555",
    closed_by_membership_id: null,
    closed_at: null,
    created_at: "2026-10-18T01:00:00Z",
  };
}

function renderPayments(
  flags: Partial<{
    canApprove: boolean;
    canCreate: boolean;
    canRefund: boolean;
    canSubmit: boolean;
    canViewReceipts: boolean;
    canVoid: boolean;
    gymName: string;
  }> = {},
) {
  return render(
    <PaymentsManager
      canApprove={flags.canApprove ?? true}
      canCreate={flags.canCreate ?? false}
      canRefund={flags.canRefund ?? true}
      canSubmit={flags.canSubmit ?? false}
      canUpdate={false}
      canViewReceipts={flags.canViewReceipts ?? false}
      canVoid={flags.canVoid ?? true}
      canVoidReceipt={false}
      currency="THB"
      defaultDate="2026-10-18"
      gymName={flags.gymName ?? "North Gym"}
    />,
  );
}

function printContext(enabled = false): ReceiptPrintContext {
  return {
    printer_enabled: enabled,
    printer_print_logo: false,
    printer_print_address: true,
    printer_print_phone: true,
    printer_footer: "See you soon",
    phone: "091234567",
    logo: null,
  };
}

function gymProfile(): GymProfile {
  return {
    address_line1: "1 Main",
    address_line2: "",
    city: "Yangon",
    region: "",
    postal_code: "",
    country_code: "",
    name: "North Gym",
  } as GymProfile;
}

function issuedReceipt(id = RECEIPT_ID): Receipt {
  return {
    id,
    payment_id: PAYMENT_ID,
    receipt_number: "RCP-41",
    issued_at: "2026-10-18T02:00:00Z",
    gym_code: "GYM",
    gym_name: "North Gym",
    gym_address: "1 Main, Yangon",
    member_number: "MEM-000001",
    member_name: "Ada Lovelace",
    description: "Locker key",
    amount: "25.00",
    currency: "THB",
    method: "CASH",
    business_date: "2026-10-18",
    issuer_name: "Owner",
    status: "ISSUED",
    created_at: "2026-10-18T02:00:00Z",
  };
}

function approvalOf(method: Payment["method"] = "CARD") {
  return {
    payment: payment({ status: "POSTED", method }),
    receipt: issuedReceipt(),
    cash_session: null,
    cash_transaction: null,
    membership_settlement: null,
  };
}

function printPopup() {
  const popup = {
    closed: false,
    close: vi.fn(),
    opener: window,
    location: { replace: vi.fn() },
  };
  vi.spyOn(window, "open").mockImplementation(() => popup as unknown as Window);
  return popup;
}

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

describe("payments manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    if (vi.isMockFunction(window.open)) vi.mocked(window.open).mockRestore();
    if (!vi.isMockFunction(window.print)) {
      vi.spyOn(window, "print").mockImplementation(() => undefined);
    }
    vi.mocked(listMembers).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listMemberships).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listReceipts).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [till()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockImplementation(async () => payment());
    vi.mocked(getReceiptPrintContext).mockResolvedValue(printContext());
    vi.mocked(getGymProfile).mockResolvedValue(gymProfile());
    vi.mocked(operationGet).mockRejectedValue(
      new Error("Member was not loaded."),
    );
  });

  afterEach(() => {
    if (vi.isMockFunction(window.open)) vi.mocked(window.open).mockRestore();
  });

  it("requires a cash session on cash approval and sends none for other methods", async () => {
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(approvePayment).mockResolvedValue({
      payment: payment({ status: "POSTED" }),
      receipt: {
        id: "66666666-6666-4666-8666-666666666666",
        payment_id: PAYMENT_ID,
        receipt_number: "R-1",
        issued_at: "2026-10-18T02:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Locker key",
        amount: "25.00",
        currency: "THB",
        method: "CASH",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T02:00:00Z",
      },
      cash_session: till(),
      cash_transaction: null,
      membership_settlement: null,
    });
    const view = renderPayments();
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    expect(approvePayment).not.toHaveBeenCalled();
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Open cash session" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    expect(approvePayment).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Record this payment" }),
    );
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {
        cash_session_id: SESSION_ID,
      });
    });

    view.unmount();
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ method: "CARD" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(payment({ method: "CARD" }));
    renderPayments();
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(screen.getByText(/no cash session is sent/i)).toBeVisible();
    finishApproval();
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {});
    });
  });

  it("sends the typed refund method and date and a typed void reason", async () => {
    vi.mocked(listPayments).mockResolvedValue({
      data: [
        payment({
          status: "POSTED",
          cash_transaction_id: "77777777-7777-4777-8777-777777777777",
        }),
      ],
      meta: { has_more: true, next_after: "pay-2" },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({
        status: "POSTED",
        cash_transaction_id: "77777777-7777-4777-8777-777777777777",
      }),
    );
    vi.mocked(refundPayment).mockResolvedValue({
      original_payment: payment({ status: "POSTED" }),
      correction_payment: payment({
        id: "88888888-8888-4888-8888-888888888888",
      }),
      original_receipt: null,
      correction_receipt: {
        id: "99999999-9999-4999-8999-999999999999",
        payment_id: PAYMENT_ID,
        receipt_number: "R-2",
        issued_at: "2026-10-18T03:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Refund",
        amount: "5.00",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T03:00:00Z",
      },
      cash_transactions: [],
      refundable_amount: "20.00",
      membership_settlement: null,
    });
    vi.mocked(voidPayment).mockResolvedValue({
      original_payment: payment({ status: "VOID" }),
      correction_payment: payment({
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      }),
      original_receipt: null,
      correction_receipt: {
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        payment_id: PAYMENT_ID,
        receipt_number: "R-3",
        issued_at: "2026-10-18T03:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Void",
        amount: "25.00",
        currency: "THB",
        method: "CASH",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T03:00:00Z",
      },
      cash_transactions: [],
      refundable_amount: "0.00",
      membership_settlement: null,
    });
    renderPayments();
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await waitFor(() => {
      expect(listPayments).toHaveBeenCalledWith(
        expect.objectContaining({ after: "pay-2" }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Refund" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Refund amount" }), {
      target: { value: "5.00" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Refund reason" }), {
      target: { value: "Member changed their mind" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Refund method" }), {
      target: { value: "CARD" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm refund" }));
    await waitFor(() => {
      expect(refundPayment).toHaveBeenCalledWith(PAYMENT_ID, {
        amount: "5.00",
        reason: "Member changed their mind",
        method: "CARD",
        business_date: "2026-10-18",
      });
    });
    expect(await screen.findByText(/20.00/)).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Void" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    expect(voidPayment).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "Void reason" }), {
      target: { value: "Recorded on the wrong day" },
    });
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Destination open session" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    await waitFor(() => {
      expect(voidPayment).toHaveBeenCalledWith(PAYMENT_ID, {
        reason: "Recorded on the wrong day",
        business_date: "2026-10-18",
        cash_session_id: SESSION_ID,
      });
    });
    expect(JSON.stringify(vi.mocked(voidPayment).mock.calls)).not.toContain(
      "by staff",
    );
  });

  it("hides corrections without permission and explains product and legacy cash", async () => {
    vi.mocked(listPayments).mockResolvedValue({
      data: [
        payment({
          status: "POSTED",
          purpose: "PRODUCT",
          retail_sale_id: SESSION_ID,
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({
        status: "POSTED",
        purpose: "PRODUCT",
        retail_sale_id: SESSION_ID,
      }),
    );
    const view = renderPayments({
      canApprove: false,
      canVoid: true,
      canRefund: true,
    });
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    expect(await screen.findByRole("note")).toHaveTextContent(
      "RETAIL_CORRECTION_UNAVAILABLE",
    );
    expect(
      screen.queryByRole("button", { name: "Void" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Refund" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();

    view.unmount();
    vi.mocked(listPayments).mockResolvedValue({
      data: [
        payment({
          status: "POSTED",
          entry_type: "REVERSAL",
          original_payment_id: PAYMENT_ID,
          description: "Reversal: Locker key",
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({
        status: "POSTED",
        entry_type: "REVERSAL",
        original_payment_id: PAYMENT_ID,
        description: "Reversal: Locker key",
      }),
    );
    renderPayments({ canApprove: false, canVoid: true, canRefund: true });
    fireEvent.click(
      await screen.findByRole("button", { name: "Reversal: Locker key" }),
    );
    expect(await screen.findByText("Reversal")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Void" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Refund" }),
    ).not.toBeInTheDocument();

    view.unmount();
    vi.mocked(getPayment).mockResolvedValue(
      payment({
        status: "POSTED",
        method: "CASH",
        cash_transaction_id: null,
      }),
    );
    vi.mocked(listPayments).mockResolvedValue({
      data: [
        payment({
          status: "POSTED",
          purpose: "OTHER",
          retail_sale_id: null,
          cash_transaction_id: null,
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    renderPayments({ canApprove: false, canVoid: true, canRefund: false });
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Void" }));
    expect(
      screen.getByText(/does not name a destination session/i),
    ).toBeVisible();
    expect(
      screen.queryByRole("combobox", { name: "Destination open session" }),
    ).not.toBeInTheDocument();
  });

  it("shows an API field error", async () => {
    vi.mocked(listPayments).mockRejectedValue(
      new OperationError("Enter an exact amount.", {
        amount: "Enter an exact amount.",
      }),
    );
    renderPayments();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter an exact amount.",
    );
  });

  it("lists matching members under the payment search as a name is typed", async () => {
    const ada = member();
    vi.mocked(listPayments).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listMembers).mockImplementation(async (query) => {
      if (query?.search === "Ada") {
        return {
          data: [ada],
          meta: { has_more: false, next_after: null },
        };
      }
      return { data: [], meta: { has_more: false, next_after: null } };
    });
    render(
      <PaymentsManager
        canApprove={false}
        canCreate
        canRefund={false}
        canSubmit={false}
        canUpdate={false}
        canViewReceipts={false}
        canVoid={false}
        canVoidReceipt={false}
        currency="MMK"
        defaultDate="2026-10-27"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Record payment" }),
    );
    expect(screen.getByText("No member selected.")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText("Name or member number"), {
      target: { value: "Ada" },
    });
    const match = await screen.findByRole("button", {
      name: "MEM-000001 — Ada Lovelace",
    });
    expect(listMembers).toHaveBeenCalledWith({ search: "Ada", limit: 10 });
    fireEvent.click(match);
    expect(
      screen.getByText("Selected member: MEM-000001 — Ada Lovelace"),
    ).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Member" })).toHaveValue(
      ada.id,
    );
    const amount = screen.getByRole("textbox", { name: "Amount (MMK)" });
    expect(amount).toBeRequired();
    fireEvent.change(amount, { target: { value: "20၀0a.5x99" } });
    expect(amount).toHaveValue("200.59");
    expect(screen.getByRole("combobox", { name: "Member" })).toBeRequired();
    expect(screen.getByRole("combobox", { name: "Membership" })).toBeRequired();
    expect(screen.getByRole("textbox", { name: "Description" })).toBeRequired();
  });

  it("shows a review-only draft receipt and does not print on submit", async () => {
    const ada = member();
    vi.mocked(getReceiptPrintContext).mockResolvedValue(printContext(true));
    vi.mocked(operationGet).mockImplementation(async (path: string) => {
      if (path === `members/${MEMBER_ID}/`) return ada;
      throw new Error(path);
    });
    vi.mocked(listPayments).mockResolvedValue({
      data: [
        payment({
          status: "DRAFT",
          member_id: MEMBER_ID,
          description: "Locker key",
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({
        status: "DRAFT",
        member_id: MEMBER_ID,
        description: "Locker key",
      }),
    );
    vi.mocked(submitPayment).mockResolvedValue(
      payment({ status: "PENDING_APPROVAL", member_id: MEMBER_ID }),
    );
    const open = vi.spyOn(window, "open").mockImplementation(() => {
      throw new Error("submit must not open a window");
    });
    renderPayments({
      canApprove: false,
      canSubmit: true,
      gymName: "North Gym",
    });

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Submit" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Submit this draft for approval?",
    });
    expect(within(dialog).getByRole("article")).toHaveAttribute(
      "data-receipt-mode",
      "draft",
    );
    expect(within(dialog).getByText("DRAFT — not a receipt")).toBeVisible();
    expect(within(dialog).getAllByText("Assigned after approval")).toHaveLength(
      2,
    );
    expect(
      within(dialog).getByText(
        "Review only. Submitting this draft does not print a receipt.",
      ),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("heading", { name: "North Gym" }),
    ).toBeVisible();
    expect(within(dialog).getByText("1 Main")).toBeVisible();
    expect(within(dialog).queryByText(/Yangon/)).not.toBeInTheDocument();
    expect(within(dialog).getByText("091234567")).toBeVisible();
    expect(within(dialog).getByText("See you soon")).toBeVisible();
    expect(await within(dialog).findByText("Ada Lovelace")).toBeVisible();
    expect(within(dialog).getByText("MEM-000001")).toBeVisible();
    expect(within(dialog).getByText("Locker key")).toBeVisible();
    expect(within(dialog).getByText("25.00 THB")).toBeVisible();
    expect(within(dialog).queryByText("Walk-in")).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Print receipt" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm submit" }),
    );
    await waitFor(() => {
      expect(submitPayment).toHaveBeenCalledWith(PAYMENT_ID);
    });
    expect(open).not.toHaveBeenCalled();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("still submits when the printer context fails and does not print", async () => {
    vi.mocked(getReceiptPrintContext).mockRejectedValue(new Error("offline"));
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ status: "DRAFT" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(payment({ status: "DRAFT" }));
    vi.mocked(submitPayment).mockResolvedValue(
      payment({ status: "PENDING_APPROVAL" }),
    );
    const open = vi.spyOn(window, "open");
    renderPayments({ canApprove: false, canSubmit: true });

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Submit" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Submit this draft for approval?",
    });
    expect(
      within(dialog).getByText("The printer setup could not be loaded."),
    ).toBeVisible();
    expect(within(dialog).getByText("DRAFT — not a receipt")).toBeVisible();
    expect(within(dialog).getAllByText("Walk-in").length).toBeGreaterThan(0);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm submit" }),
    );
    await waitFor(() => {
      expect(submitPayment).toHaveBeenCalledWith(PAYMENT_ID);
    });
    expect(open).not.toHaveBeenCalled();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("opens the final receipt only after approval when the printer is enabled", async () => {
    const popup = printPopup();
    vi.mocked(getReceiptPrintContext).mockResolvedValue(printContext(true));
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ method: "CARD", status: "PENDING_APPROVAL" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({ method: "CARD", status: "PENDING_APPROVAL" }),
    );
    vi.mocked(approvePayment).mockResolvedValue(approvalOf("CARD"));
    renderPayments();

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    finishApproval();

    expect(window.open).toHaveBeenCalledTimes(1);
    expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {});
    });
    await waitFor(() => {
      expect(popup.location.replace).toHaveBeenCalledWith(PRINT_HREF);
    });
    expect(popup.close).not.toHaveBeenCalled();
    expect(PRINT_HREF).not.toContain(PAYMENT_ID);
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
  });

  it("does not print when the printer is disabled or the print context fails", async () => {
    const open = vi.spyOn(window, "open");
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ method: "CARD", status: "PENDING_APPROVAL" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({ method: "CARD", status: "PENDING_APPROVAL" }),
    );
    vi.mocked(approvePayment).mockResolvedValue(approvalOf("CARD"));
    const view = renderPayments();

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    finishApproval();
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {});
    });
    expect(open).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();

    view.unmount();
    open.mockClear();
    vi.mocked(approvePayment).mockClear();
    vi.mocked(getReceiptPrintContext).mockRejectedValue(new Error("offline"));
    renderPayments();
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(
      screen.getByText("The printer setup could not be loaded."),
    ).toBeVisible();
    finishApproval();
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {});
    });
    expect(open).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("link", { name: "Print receipt" }),
    ).toHaveAttribute("href", PRINT_HREF);
    expect(window.print).not.toHaveBeenCalled();
  });

  it("approves when the print window is blocked and offers a print link", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(getReceiptPrintContext).mockResolvedValue(printContext(true));
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ method: "CARD", status: "PENDING_APPROVAL" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({ method: "CARD", status: "PENDING_APPROVAL" }),
    );
    vi.mocked(approvePayment).mockResolvedValue(approvalOf("CARD"));
    renderPayments();

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    finishApproval();
    await waitFor(() => {
      expect(approvePayment).toHaveBeenCalledWith(PAYMENT_ID, {});
    });
    const fallback = await screen.findByRole("link", { name: "Print receipt" });
    expect(fallback).toHaveAttribute("href", PRINT_HREF);
    expect(fallback).toHaveAttribute("target", "_blank");
    expect(screen.getByText("The print window was blocked.")).toBeVisible();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("does not print when approval fails", async () => {
    const popup = printPopup();
    vi.mocked(getReceiptPrintContext).mockResolvedValue(printContext(true));
    vi.mocked(listPayments).mockResolvedValue({
      data: [payment({ method: "CARD", status: "PENDING_APPROVAL" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(
      payment({ method: "CARD", status: "PENDING_APPROVAL" }),
    );
    vi.mocked(approvePayment).mockRejectedValue(
      new OperationError("The till is closed."),
    );
    renderPayments();

    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    finishApproval();
    expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The till is closed.",
    );
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("reprints a receipt beside download and hides it without receipt access", async () => {
    vi.mocked(listPayments).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    const hidden = renderPayments();
    expect(
      await screen.findByRole("heading", { name: "Payment ledger" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("tab", { name: "Receipts" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    hidden.unmount();

    vi.mocked(listReceipts).mockResolvedValue({
      data: [issuedReceipt()],
      meta: { has_more: false, next_after: null },
    });
    renderPayments({ canViewReceipts: true });
    fireEvent.click(await screen.findByRole("tab", { name: "Receipts" }));
    fireEvent.click(await screen.findByRole("button", { name: "RCP-41" }));
    expect(
      screen.getByRole("button", { name: "Download receipt" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Print receipt" })).toHaveAttribute(
      "href",
      `/receipts/${RECEIPT_ID}/print`,
    );
    expect(screen.getByRole("link", { name: "Print receipt" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(screen.getByRole("link", { name: "Print receipt" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });

  it("prints a posted receipt and refund correction only with receipt access", async () => {
    const posted = payment({ status: "POSTED", method: "CARD" });
    const correctionId = "99999999-9999-4999-8999-999999999999";
    vi.mocked(listPayments).mockResolvedValue({
      data: [posted],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getPayment).mockResolvedValue(posted);
    const hidden = renderPayments();
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    expect(await screen.findByRole("button", { name: "Refund" })).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    expect(listReceipts).not.toHaveBeenCalled();
    hidden.unmount();

    vi.mocked(listReceipts).mockResolvedValue({
      data: [issuedReceipt()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(refundPayment).mockResolvedValue({
      original_payment: posted,
      correction_payment: payment({
        id: "88888888-8888-4888-8888-888888888888",
        status: "POSTED",
        method: "CARD",
      }),
      original_receipt: null,
      correction_receipt: {
        ...issuedReceipt(correctionId),
        receipt_number: "R-2",
        description: "Refund",
      },
      cash_transactions: [],
      refundable_amount: "20.00",
      membership_settlement: null,
    });
    renderPayments({ canViewReceipts: true });
    fireEvent.click(await screen.findByRole("button", { name: "Locker key" }));
    const postedLink = await screen.findByRole("link", {
      name: "Print receipt",
    });
    expect(postedLink).toHaveAttribute("href", `/receipts/${RECEIPT_ID}/print`);
    expect(postedLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(postedLink).toHaveAttribute("target", "_blank");
    expect(listReceipts).toHaveBeenCalledWith({
      payment: PAYMENT_ID,
      status: "ISSUED",
      limit: "5",
    });

    fireEvent.click(screen.getByRole("button", { name: "Refund" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Refund amount" }), {
      target: { value: "5.00" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Refund reason" }), {
      target: { value: "Member changed their mind" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Refund method" }), {
      target: { value: "CARD" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm refund" }));
    await waitFor(() => {
      expect(
        screen.getAllByRole("link", { name: "Print receipt" }),
      ).toHaveLength(2);
    });
    expect(
      screen
        .getAllByRole("link", { name: "Print receipt" })
        .map((link) => link.getAttribute("href")),
    ).toEqual(
      expect.arrayContaining([
        `/receipts/${RECEIPT_ID}/print`,
        `/receipts/${correctionId}/print`,
      ]),
    );
  });
});

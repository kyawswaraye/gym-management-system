import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approvePayment,
  getPayment,
  listCashSessions,
  listPayments,
  listReceipts,
  refundPayment,
  voidPayment,
} from "@/features/finance/client";
import { PaymentsManager } from "@/features/finance/payments-manager";
import { listMembers, listMemberships } from "@/features/operations/client";
import { OperationError } from "@/features/operations/client";
import type { CashSession, Payment } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  approvePayment: vi.fn(),
  cancelPayment: vi.fn(),
  createPayment: vi.fn(),
  downloadReceiptDocument: vi.fn(),
  getPayment: vi.fn(),
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
  listMembers: vi.fn(),
  listMemberships: vi.fn(),
}));

const PAYMENT_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";

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
    canVoid: boolean;
    canRefund: boolean;
  }> = {},
) {
  return render(
    <PaymentsManager
      canApprove={flags.canApprove ?? true}
      canCreate={false}
      canRefund={flags.canRefund ?? true}
      canSubmit={false}
      canUpdate={false}
      canViewReceipts={false}
      canVoid={flags.canVoid ?? true}
      canVoidReceipt={false}
      currency="THB"
      defaultDate="2026-10-18"
    />,
  );
}

describe("payments manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
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
});

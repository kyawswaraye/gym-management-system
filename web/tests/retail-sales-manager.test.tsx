import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveRetailSale,
  getRetailSale,
  listCashSessions,
  listProducts,
  listRetailSales,
  voidRetailSale,
} from "@/features/finance/client";
import { RetailSalesManager } from "@/features/finance/retail-sales-manager";
import { listMembers } from "@/features/operations/client";
import type { CashSession, Product, RetailSale } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  approveRetailSale: vi.fn(),
  cancelRetailSale: vi.fn(),
  createRetailSale: vi.fn(),
  getRetailSale: vi.fn(),
  listCashSessions: vi.fn(),
  listProducts: vi.fn(),
  listRetailSales: vi.fn(),
  rejectRetailSale: vi.fn(),
  submitRetailSale: vi.fn(),
  updateRetailSale: vi.fn(),
  voidRetailSale: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  listMembers: vi.fn(),
}));

const SALE_ID = "11111111-1111-4111-8111-111111111111";
const PRODUCT_ID = "22222222-2222-4222-8222-222222222222";
const SESSION_ID = "33333333-3333-4333-8333-333333333333";
const PAYMENT_ID = "44444444-4444-4444-8444-444444444444";

function product(): Product {
  return {
    id: PRODUCT_ID,
    sku: "WATER",
    barcode: null,
    name: "Water",
    category_id: SESSION_ID,
    unit: "PIECE",
    sale_price: "0.10",
    current_unit_cost: "0.04",
    default_unit_cost: "0.04",
    low_stock_threshold: "1.000",
    quantity_on_hand: "10.000",
    sold_quantity: "0.000",
    revenue: "0.00",
    cost_of_goods_sold: "0.00",
    gross_profit: "0.00",
    is_active: true,
    archived_at: null,
    created_by_membership_id: SESSION_ID,
    updated_by_membership_id: SESSION_ID,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
  };
}

function sale(overrides: Partial<RetailSale> = {}): RetailSale {
  return {
    id: SALE_ID,
    member_id: null,
    business_date: "2026-10-18",
    currency: "THB",
    payment_method: "CASH",
    reference_number: "",
    status: "PENDING_APPROVAL",
    subtotal: "0.13",
    discount_total: "0.00",
    total_amount: "0.13",
    cogs_total: "0.05",
    gross_profit_total: "0.08",
    payment_id: null,
    receipt_id: null,
    rejection_reason: "",
    reason: "",
    submitted_at: "2026-10-18T02:00:00Z",
    submitted_by_membership_id: SESSION_ID,
    approved_at: null,
    approved_by_membership_id: null,
    rejected_at: null,
    rejected_by_membership_id: null,
    posted_at: null,
    posted_by_membership_id: null,
    cancelled_at: null,
    cancelled_by_membership_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    void_business_date: null,
    void_reason: "",
    created_by_membership_id: SESSION_ID,
    updated_by_membership_id: SESSION_ID,
    created_at: "2026-10-18T02:00:00Z",
    updated_at: "2026-10-18T02:00:00Z",
    lines: [
      {
        id: "55555555-5555-4555-8555-555555555555",
        product_id: PRODUCT_ID,
        quantity: "1.250",
        discount: "0.00",
        sku: "WATER",
        product_name: "Water",
        unit_price: "0.10",
        unit_cost: "0.04",
        line_revenue: "0.13",
        cost_of_goods_sold: "0.05",
        gross_profit: "0.08",
      },
    ],
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
    opened_by_membership_id: SESSION_ID,
    closed_by_membership_id: null,
    closed_at: null,
    created_at: "2026-10-18T01:00:00Z",
  };
}

describe("retail sales", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMembers).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listProducts).mockResolvedValue({
      data: [product()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [till()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listRetailSales).mockResolvedValue({
      data: [sale()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getRetailSale).mockResolvedValue(sale());
  });

  it("requires a cash session and never offers a partial return", async () => {
    vi.mocked(approveRetailSale).mockResolvedValue({
      retail_sale: sale({ status: "POSTED", payment_id: PAYMENT_ID }),
      payment: {
        id: PAYMENT_ID,
        member_id: null,
        membership_id: null,
        original_payment_id: null,
        reversal_payment_id: null,
        entry_type: "CHARGE",
        purpose: "PRODUCT",
        description: "Water",
        amount: "0.13",
        currency: "THB",
        method: "CASH",
        business_date: "2026-10-18",
        received_at: "2026-10-18T02:00:00Z",
        reference_number: "",
        status: "POSTED",
        rejection_reason: "",
        cancellation_reason: "",
        void_reason: "",
        submitted_at: null,
        submitted_by_membership_id: null,
        approved_at: "2026-10-18T02:00:00Z",
        approved_by_membership_id: SESSION_ID,
        rejected_at: null,
        rejected_by_membership_id: null,
        cancelled_at: null,
        cancelled_by_membership_id: null,
        posted_at: "2026-10-18T02:00:00Z",
        posted_by_membership_id: SESSION_ID,
        voided_at: null,
        voided_by_membership_id: null,
        cash_session_id: SESSION_ID,
        cash_transaction_id: SESSION_ID,
        retail_sale_id: SALE_ID,
        created_by_membership_id: SESSION_ID,
        updated_by_membership_id: SESSION_ID,
        created_at: "2026-10-18T02:00:00Z",
        updated_at: "2026-10-18T02:00:00Z",
      },
      receipt: {
        id: "66666666-6666-4666-8666-666666666666",
        payment_id: PAYMENT_ID,
        receipt_number: "R-9",
        issued_at: "2026-10-18T02:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Water",
        amount: "0.13",
        currency: "THB",
        method: "CASH",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T02:00:00Z",
      },
      cash_transaction: null,
    });
    render(
      <RetailSalesManager
        canApprove
        canCreate
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="THB"
        defaultDate="2026-10-18"
      />,
    );
    expect(
      screen.queryByRole("button", { name: /partial return/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/partial return/i)).not.toBeInTheDocument();
    await screen.findByRole("option", { name: /WATER/ });
    fireEvent.change(screen.getByRole("combobox", { name: "Product" }), {
      target: { value: PRODUCT_ID },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Quantity" }), {
      target: { value: "1.25" },
    });
    expect(await screen.findByText(/Preview only: 0.13/)).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "2026-10-18" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    expect(approveRetailSale).not.toHaveBeenCalled();
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Open cash session" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() => {
      expect(approveRetailSale).toHaveBeenCalledWith(SALE_ID, {
        cash_session_id: SESSION_ID,
      });
    });
    expect(await screen.findByText("R-9")).toBeVisible();
  });

  it("sends a destination session only for a cash void", async () => {
    vi.mocked(getRetailSale).mockResolvedValue(
      sale({ status: "POSTED", payment_method: "CARD" }),
    );
    vi.mocked(listRetailSales).mockResolvedValue({
      data: [sale({ status: "POSTED", payment_method: "CARD" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(voidRetailSale).mockResolvedValue({
      retail_sale: sale({ status: "VOID", payment_method: "CARD" }),
      original_payment: {
        id: PAYMENT_ID,
        member_id: null,
        membership_id: null,
        original_payment_id: null,
        reversal_payment_id: null,
        entry_type: "CHARGE",
        purpose: "PRODUCT",
        description: "Water",
        amount: "0.13",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        received_at: "2026-10-18T02:00:00Z",
        reference_number: "",
        status: "VOID",
        rejection_reason: "",
        cancellation_reason: "",
        void_reason: "Wrong item",
        submitted_at: null,
        submitted_by_membership_id: null,
        approved_at: null,
        approved_by_membership_id: null,
        rejected_at: null,
        rejected_by_membership_id: null,
        cancelled_at: null,
        cancelled_by_membership_id: null,
        posted_at: null,
        posted_by_membership_id: null,
        voided_at: "2026-10-18T03:00:00Z",
        voided_by_membership_id: SESSION_ID,
        cash_session_id: null,
        cash_transaction_id: null,
        retail_sale_id: SALE_ID,
        created_by_membership_id: SESSION_ID,
        updated_by_membership_id: SESSION_ID,
        created_at: "2026-10-18T02:00:00Z",
        updated_at: "2026-10-18T03:00:00Z",
      },
      reversal_payment: {
        id: "77777777-7777-4777-8777-777777777777",
        member_id: null,
        membership_id: null,
        original_payment_id: PAYMENT_ID,
        reversal_payment_id: null,
        entry_type: "REVERSAL",
        purpose: "PRODUCT",
        description: "Water",
        amount: "0.13",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        received_at: "2026-10-18T03:00:00Z",
        reference_number: "",
        status: "POSTED",
        rejection_reason: "",
        cancellation_reason: "",
        void_reason: "",
        submitted_at: null,
        submitted_by_membership_id: null,
        approved_at: null,
        approved_by_membership_id: null,
        rejected_at: null,
        rejected_by_membership_id: null,
        cancelled_at: null,
        cancelled_by_membership_id: null,
        posted_at: "2026-10-18T03:00:00Z",
        posted_by_membership_id: SESSION_ID,
        voided_at: null,
        voided_by_membership_id: null,
        cash_session_id: null,
        cash_transaction_id: null,
        retail_sale_id: SALE_ID,
        created_by_membership_id: SESSION_ID,
        updated_by_membership_id: SESSION_ID,
        created_at: "2026-10-18T03:00:00Z",
        updated_at: "2026-10-18T03:00:00Z",
      },
      original_receipt: null,
      reversal_receipt: {
        id: "88888888-8888-4888-8888-888888888888",
        payment_id: PAYMENT_ID,
        receipt_number: "R-10",
        issued_at: "2026-10-18T03:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Water",
        amount: "0.13",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T03:00:00Z",
      },
      cash_transaction: null,
      stock_movements: [
        {
          id: "99999999-9999-4999-8999-999999999999",
          retail_sale_line_id: "55555555-5555-4555-8555-555555555555",
          product_id: PRODUCT_ID,
          quantity: "1.250",
          movement_type: "SALE_RETURN",
          unit_cost_snapshot: "0.04",
          business_date: "2026-10-18",
          reverses_movement_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
      ],
    });
    render(
      <RetailSalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="THB"
        defaultDate="2026-10-18"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "2026-10-18" }));
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    expect(
      screen.queryByRole("combobox", { name: "Destination open session" }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Void reason" }), {
      target: { value: "Wrong item" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    await waitFor(() => {
      expect(voidRetailSale).toHaveBeenCalledWith(SALE_ID, {
        reason: "Wrong item",
        business_date: "2026-10-18",
      });
    });
    expect(await screen.findByText("1")).toBeVisible();
  });
});

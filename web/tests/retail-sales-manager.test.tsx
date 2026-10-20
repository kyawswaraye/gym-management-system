import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveRetailSale,
  getRetailSale,
  listCashSessions,
  listProducts,
  listRetailSales,
  returnRetailSale,
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
  returnRetailSale: vi.fn(),
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
    returned_amount: "0.00",
    return_count: 0,
    lines: [
      {
        id: "55555555-5555-4555-8555-555555555555",
        product_id: PRODUCT_ID,
        quantity: "1.250",
        returned_quantity: "0.000",
        remaining_quantity: "1.250",
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
        pt_sale_id: null,
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
        pt_sale_id: null,
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
        pt_sale_id: null,
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

  it("returns a posted card sale without a cash session or client money", async () => {
    const posted = sale({
      status: "POSTED",
      payment_method: "CARD",
      payment_id: PAYMENT_ID,
      receipt_id: "88888888-8888-4888-8888-888888888888",
      lines: [
        {
          id: "55555555-5555-4555-8555-555555555555",
          product_id: PRODUCT_ID,
          quantity: "1.250",
          returned_quantity: "0.000",
          remaining_quantity: "1.250",
          discount: "0.00",
          sku: "WATER",
          product_name: "Water",
          unit_price: "0.10",
          unit_cost: "0.04",
          line_revenue: "0.13",
          cost_of_goods_sold: "0.05",
          gross_profit: "0.08",
        },
        {
          id: "66666666-6666-4666-8666-666666666666",
          product_id: PRODUCT_ID,
          quantity: "1.000",
          returned_quantity: "1.000",
          remaining_quantity: "0.000",
          discount: "0.00",
          sku: "TOWEL",
          product_name: "Towel",
          unit_price: "1.00",
          unit_cost: "0.40",
          line_revenue: "1.00",
          cost_of_goods_sold: "0.40",
          gross_profit: "0.60",
        },
      ],
    });
    vi.mocked(getRetailSale).mockResolvedValue(posted);
    vi.mocked(listRetailSales).mockResolvedValue({
      data: [posted],
      meta: { has_more: false, next_after: null },
    });
    const returned = sale({
      ...posted,
      returned_amount: "3.33",
      return_count: 1,
      lines: [
        {
          ...posted.lines[0]!,
          returned_quantity: "1.000",
          remaining_quantity: "0.250",
        },
        posted.lines[1]!,
      ],
    });
    let finish: (
      value: Awaited<ReturnType<typeof returnRetailSale>>,
    ) => void = () => undefined;
    vi.mocked(returnRetailSale).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
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
    expect(
      await screen.findByRole("button", { name: "Void sale" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Partial return" }));
    expect(
      screen.getByText("Sold 1.250, returned 0.000, remaining 1.250"),
    ).toBeVisible();
    expect(
      screen.getByText("Sold 1.000, returned 1.000, remaining 0.000"),
    ).toBeVisible();
    expect(
      screen.queryByRole("combobox", { name: "Destination open session" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Return quantity for Towel" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Return quantity for Water" }),
      { target: { value: "2" } },
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Return reason" }), {
      target: { value: "Damaged" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm return" }));
    expect(returnRetailSale).not.toHaveBeenCalled();
    expect(
      screen.getByText("Enter no more than the remaining 1.250."),
    ).toBeVisible();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Return quantity for Water" }),
      { target: { value: "1.25" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm return" }));
    expect(screen.getByRole("status")).toHaveTextContent("Returning the sale");
    expect(
      screen.getByRole("button", { name: "Confirm return" }),
    ).toBeDisabled();
    finish({
      retail_sale: returned,
      retail_return: {
        id: "abababab-abab-4bab-8bab-abababababab",
        sale_id: SALE_ID,
        business_date: "2026-10-18",
        reason: "Damaged",
        currency: "THB",
        payment_method: "CARD",
        refund_amount: "3.33",
        discount_total: "0.00",
        cogs_total: "0.04",
        gross_profit_total: "3.29",
        payment_id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
        receipt_id: "dededede-dede-4ede-8ede-dededededede",
        cash_session_id: null,
        returned_by_membership_id: SESSION_ID,
        lines: [],
      },
      refund_payment: {
        id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
        member_id: null,
        membership_id: null,
        pt_sale_id: null,
        original_payment_id: PAYMENT_ID,
        reversal_payment_id: null,
        entry_type: "REFUND",
        purpose: "PRODUCT",
        description: "Water",
        amount: "3.33",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        received_at: "2026-10-18T04:00:00Z",
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
        posted_at: "2026-10-18T04:00:00Z",
        posted_by_membership_id: SESSION_ID,
        voided_at: null,
        voided_by_membership_id: null,
        cash_session_id: null,
        cash_transaction_id: null,
        retail_sale_id: SALE_ID,
        created_by_membership_id: SESSION_ID,
        updated_by_membership_id: SESSION_ID,
        created_at: "2026-10-18T04:00:00Z",
        updated_at: "2026-10-18T04:00:00Z",
      },
      correction_receipt: {
        id: "dededede-dede-4ede-8ede-dededededede",
        payment_id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
        receipt_number: "R-RET",
        issued_at: "2026-10-18T04:00:00Z",
        gym_code: "GYM",
        gym_name: "Gym",
        gym_address: "",
        member_number: "",
        member_name: "",
        description: "Water",
        amount: "3.33",
        currency: "THB",
        method: "CARD",
        business_date: "2026-10-18",
        issuer_name: "Owner",
        status: "ISSUED",
        created_at: "2026-10-18T04:00:00Z",
      },
      cash_transaction: null,
      stock_movements: [
        {
          id: "99999999-9999-4999-8999-999999999999",
          retail_sale_line_id: "55555555-5555-4555-8555-555555555555",
          product_id: PRODUCT_ID,
          quantity: "1.000",
          movement_type: "SALE_RETURN",
          unit_cost_snapshot: "0.04",
          business_date: "2026-10-18",
          reverses_movement_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
      ],
    });
    await waitFor(() => {
      expect(returnRetailSale).toHaveBeenCalledWith(SALE_ID, {
        reason: "Damaged",
        business_date: "2026-10-18",
        lines: [
          {
            line_id: "55555555-5555-4555-8555-555555555555",
            quantity: "1.250",
          },
        ],
      });
    });
    expect(screen.getByText("Refund amount").parentElement).toHaveTextContent(
      "3.33",
    );
    expect(screen.getByText("R-RET")).toBeVisible();
    expect(
      screen.getByText("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd"),
    ).toBeVisible();
    expect(screen.getByText("Cash movement").parentElement).toHaveTextContent(
      "None",
    );
    expect(
      screen.queryByRole("button", { name: "Void sale" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent(
      "remaining 0.250",
    );
    expect(
      screen.getByRole("button", { name: "Partial return" }),
    ).toBeVisible();
  });

  it("sends a cash session only when one is chosen", async () => {
    const posted = sale({ status: "POSTED", payment_method: "CASH" });
    vi.mocked(getRetailSale).mockResolvedValue(posted);
    vi.mocked(listRetailSales).mockResolvedValue({
      data: [posted],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(returnRetailSale).mockResolvedValue({
      retail_sale: posted,
      retail_return: {
        id: "abababab-abab-4bab-8bab-abababababab",
        sale_id: SALE_ID,
        business_date: "2026-10-18",
        reason: "Change of mind",
        currency: "THB",
        payment_method: "CASH",
        refund_amount: "0.00",
        discount_total: "0.00",
        cogs_total: "0.00",
        gross_profit_total: "0.00",
        payment_id: null,
        receipt_id: null,
        cash_session_id: null,
        returned_by_membership_id: SESSION_ID,
        lines: [],
      },
      refund_payment: null,
      correction_receipt: null,
      cash_transaction: null,
      stock_movements: [],
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
    fireEvent.click(
      await screen.findByRole("button", { name: "Partial return" }),
    );
    fireEvent.change(screen.getByLabelText("Business date"), {
      target: { value: "" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Return quantity for Water" }),
      { target: { value: "0.500" } },
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Return reason" }), {
      target: { value: "Change of mind" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm return" }));
    await waitFor(() => {
      expect(returnRetailSale).toHaveBeenCalledWith(SALE_ID, {
        reason: "Change of mind",
        lines: [
          {
            line_id: "55555555-5555-4555-8555-555555555555",
            quantity: "0.500",
          },
        ],
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "Partial return" }));
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Destination open session" }),
      { target: { value: SESSION_ID } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Return quantity for Water" }),
      { target: { value: "0.250" } },
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Return reason" }), {
      target: { value: "Second piece" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm return" }));
    await waitFor(() => {
      expect(returnRetailSale).toHaveBeenLastCalledWith(SALE_ID, {
        reason: "Second piece",
        business_date: "2026-10-18",
        cash_session_id: SESSION_ID,
        lines: [
          {
            line_id: "55555555-5555-4555-8555-555555555555",
            quantity: "0.250",
          },
        ],
      });
    });
  });
});

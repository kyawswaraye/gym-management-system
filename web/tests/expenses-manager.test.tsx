import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveExpense,
  getExpense,
  listCashSessions,
  listExpenseCategories,
  listExpenses,
  voidExpense,
} from "@/features/finance/client";
import { ExpensesManager } from "@/features/finance/expenses-manager";
import type { CashSession, Expense, ExpenseCategory } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  approveExpense: vi.fn(),
  cancelExpense: vi.fn(),
  createExpense: vi.fn(),
  createExpenseCategory: vi.fn(),
  getExpense: vi.fn(),
  listCashSessions: vi.fn(),
  listExpenseCategories: vi.fn(),
  listExpenses: vi.fn(),
  rejectExpense: vi.fn(),
  submitExpense: vi.fn(),
  updateExpense: vi.fn(),
  updateExpenseCategory: vi.fn(),
  voidExpense: vi.fn(),
}));

vi.mock("@/features/finance/receipt-file", () => ({
  uploadExpenseReceipt: vi.fn(),
}));

const EXPENSE_ID = "11111111-1111-4111-8111-111111111111";
const CATEGORY_ID = "22222222-2222-4222-8222-222222222222";
const SALARY_CATEGORY_ID = "33333333-3333-4333-8333-333333333333";
const SESSION_ID = "44444444-4444-4444-8444-444444444444";

function category(overrides: Partial<ExpenseCategory> = {}): ExpenseCategory {
  return {
    id: CATEGORY_ID,
    code: "SUPPLIES",
    name: "Supplies",
    system_key: null,
    display_order: 1,
    is_active: true,
    archived_at: null,
    created_by_membership_id: null,
    updated_by_membership_id: null,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

function expense(overrides: Partial<Expense> = {}): Expense {
  return {
    id: EXPENSE_ID,
    category_id: CATEGORY_ID,
    business_date: "2026-10-18",
    description: "Cleaning supplies",
    amount: "12.50",
    currency: "THB",
    payment_method: "CASH",
    receipt_file_asset_id: null,
    policy_note: "",
    status: "PENDING_APPROVAL",
    reversal_expense_id: null,
    original_expense_id: null,
    submitted_at: "2026-10-18T02:00:00Z",
    submitted_by_membership_id: SESSION_ID,
    approved_at: null,
    approved_by_membership_id: null,
    rejected_at: null,
    rejected_by_membership_id: null,
    rejection_reason: "",
    posted_at: null,
    posted_by_membership_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    cancelled_at: null,
    cancelled_by_membership_id: null,
    reason: "",
    cash_session_id: null,
    cash_transaction_id: null,
    created_by_membership_id: SESSION_ID,
    updated_by_membership_id: SESSION_ID,
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
    opened_by_membership_id: SESSION_ID,
    closed_by_membership_id: null,
    closed_at: null,
    created_at: "2026-10-18T01:00:00Z",
  };
}

describe("expenses", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [till()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listExpenseCategories).mockResolvedValue({
      data: [
        category(),
        category({
          id: SALARY_CATEGORY_ID,
          code: "STAFF_SALARY",
          name: "Staff salary",
          system_key: "STAFF_SALARY",
        }),
      ],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getExpense).mockResolvedValue(expense());
  });

  it("keeps protected categories immutable and requires a cash session", async () => {
    vi.mocked(listExpenses).mockResolvedValue({
      data: [expense()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(approveExpense).mockResolvedValue({
      expense: expense({ status: "POSTED" }),
      cash_session: till(),
      cash_transaction: null,
    });
    render(
      <ExpensesManager
        canApprove
        canCreate={false}
        canManageCategories
        canSubmit={false}
        canUpdate={false}
        canUploadReceipt={false}
        canVoid={false}
        currency="THB"
        gymId="99999999-9999-4999-8999-999999999999"
        defaultDate="2026-10-18"
      />,
    );
    expect(
      await screen.findByRole("button", { name: "Edit Supplies" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Edit Staff salary" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cleaning supplies" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    expect(approveExpense).not.toHaveBeenCalled();
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Open cash session" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() => {
      expect(approveExpense).toHaveBeenCalledWith(EXPENSE_ID, {
        cash_session_id: SESSION_ID,
      });
    });
  });

  it("explains a protected salary expense and hides its void", async () => {
    vi.mocked(listExpenses).mockResolvedValue({
      data: [expense({ category_id: SALARY_CATEGORY_ID, status: "POSTED" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getExpense).mockResolvedValue(
      expense({ category_id: SALARY_CATEGORY_ID, status: "POSTED" }),
    );
    render(
      <ExpensesManager
        canApprove={false}
        canCreate={false}
        canManageCategories={false}
        canSubmit={false}
        canUpdate={false}
        canUploadReceipt={false}
        canVoid
        currency="THB"
        gymId="99999999-9999-4999-8999-999999999999"
        defaultDate="2026-10-18"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Cleaning supplies" }),
    );
    expect(await screen.findByRole("note")).toHaveTextContent(/salary period/i);
    expect(
      screen.queryByRole("button", { name: "Void" }),
    ).not.toBeInTheDocument();
    expect(voidExpense).not.toHaveBeenCalled();
  });
});

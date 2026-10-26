import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveSalaryPeriod,
  getSalaryPeriod,
  listCashSessions,
  listSalaryPeriods,
  replaceSalaryLines,
  voidSalaryPeriod,
} from "@/features/finance/client";
import { SalariesManager } from "@/features/finance/salaries-manager";
import { listStaff } from "@/features/operations/client";
import type {
  CashSession,
  SalaryPeriod,
  StaffRecord,
  StaffSalaryLine,
} from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  approveSalaryPeriod: vi.fn(),
  cancelSalaryPeriod: vi.fn(),
  createSalaryPeriod: vi.fn(),
  getSalaryPeriod: vi.fn(),
  listCashSessions: vi.fn(),
  listSalaryPeriods: vi.fn(),
  rejectSalaryPeriod: vi.fn(),
  replaceSalaryLines: vi.fn(),
  submitSalaryPeriod: vi.fn(),
  updateSalaryPeriod: vi.fn(),
  voidSalaryPeriod: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  listStaff: vi.fn(),
}));

const PERIOD_ID = "11111111-1111-4111-8111-111111111111";
const CASH_LINE_ID = "22222222-2222-4222-8222-222222222222";
const BANK_LINE_ID = "33333333-3333-4333-8333-333333333333";
const STAFF_A = "44444444-4444-4444-8444-444444444444";
const STAFF_B = "55555555-5555-4555-8555-555555555555";
const SESSION_ID = "66666666-6666-4666-8666-666666666666";

function line(overrides: Partial<StaffSalaryLine> = {}): StaffSalaryLine {
  return {
    id: CASH_LINE_ID,
    staff_profile_id: STAFF_A,
    staff: { id: STAFF_A, staff_code: "S-1", display_name: "Nilar" },
    amount: "100.00",
    currency: "THB",
    payment_method: "CASH",
    external_reference: "",
    cash_session_id: null,
    expense_id: null,
    description: "October salary",
    created_by_membership_id: STAFF_A,
    updated_by_membership_id: STAFF_A,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

function period(overrides: Partial<SalaryPeriod> = {}): SalaryPeriod {
  return {
    id: PERIOD_ID,
    period_start: "2026-10-01",
    period_end: "2026-10-31",
    business_date: "2026-10-18",
    description: "October payroll",
    status: "DRAFT",
    submitted_at: null,
    submitted_by_membership_id: null,
    approved_at: null,
    approved_by_membership_id: null,
    rejected_at: null,
    rejected_by_membership_id: null,
    rejection_reason: "",
    posted_at: null,
    posted_by_membership_id: null,
    cancelled_at: null,
    cancelled_by_membership_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    void_business_date: null,
    reason: "",
    created_by_membership_id: STAFF_A,
    updated_by_membership_id: STAFF_A,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    lines: [
      line(),
      line({
        id: BANK_LINE_ID,
        staff_profile_id: STAFF_B,
        staff: { id: STAFF_B, staff_code: "S-2", display_name: "Ko Ko" },
        payment_method: "BANK_TRANSFER",
        amount: "80.00",
      }),
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
    opened_by_membership_id: STAFF_A,
    closed_by_membership_id: null,
    closed_at: null,
    created_at: "2026-10-18T01:00:00Z",
  };
}

describe("salaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [till()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listStaff).mockResolvedValue({
      data: [
        { id: STAFF_A, staff_code: "S-1", display_name: "Nilar" },
        { id: STAFF_B, staff_code: "S-2", display_name: "Ko Ko" },
      ] as StaffRecord[],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listSalaryPeriods).mockResolvedValue({
      data: [period()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getSalaryPeriod).mockResolvedValue(period());
  });

  it("replaces unique staff lines with exact amounts and maps only cash lines", async () => {
    vi.mocked(replaceSalaryLines).mockResolvedValue(period());
    render(
      <SalariesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate
        canVoid
        currency="THB"
        defaultDate="2026-10-18"
      />,
    );
    expect(screen.getByText(/only those the server returns/i)).toBeVisible();
    expect(screen.queryByText(/payroll total/i)).not.toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "2026-10-01 – 2026-10-31" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit staff lines" }),
    );
    const amounts = await screen.findAllByRole("textbox", {
      name: "Amount (THB)",
    });
    fireEvent.change(amounts[0]!, { target: { value: "120.5" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Replace staff lines" }),
    );
    await waitFor(() => {
      expect(replaceSalaryLines).toHaveBeenCalledWith(PERIOD_ID, {
        lines: [
          {
            staff_profile_id: STAFF_A,
            amount: "120.50",
            currency: "THB",
            payment_method: "CASH",
            description: "October salary",
          },
          {
            staff_profile_id: STAFF_B,
            amount: "80.00",
            currency: "THB",
            payment_method: "BANK_TRANSFER",
            description: "October salary",
          },
        ],
      });
    });
  });

  it("maps only cash lines when approving", async () => {
    vi.mocked(listSalaryPeriods).mockResolvedValue({
      data: [period({ status: "PENDING_APPROVAL" })],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getSalaryPeriod).mockResolvedValue(
      period({ status: "PENDING_APPROVAL" }),
    );
    vi.mocked(approveSalaryPeriod).mockResolvedValue({
      salary_period: period({ status: "POSTED" }),
      expenses: [],
      cash_transactions: [],
    });
    render(
      <SalariesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="THB"
        defaultDate="2026-10-18"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "2026-10-01 – 2026-10-31" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(screen.getByText(/Ko Ko is BANK_TRANSFER/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    expect(approveSalaryPeriod).not.toHaveBeenCalled();
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Session for Nilar" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() => {
      expect(approveSalaryPeriod).toHaveBeenCalledWith(PERIOD_ID, {
        cash_sessions: [{ line_id: CASH_LINE_ID, cash_session_id: SESSION_ID }],
      });
    });
  });

  it("hides approval without permission and voids every cash line", async () => {
    vi.mocked(getSalaryPeriod).mockResolvedValue(period({ status: "POSTED" }));
    vi.mocked(voidSalaryPeriod).mockResolvedValue({
      salary_period: period({ status: "VOID" }),
      reversals: [],
    });
    render(
      <SalariesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="THB"
        defaultDate="2026-10-18"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "2026-10-01 – 2026-10-31" }),
    );
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Void period" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Void reason" }), {
      target: { value: "Paid the wrong month" },
    });
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Destination session for Nilar" }),
      {
        target: { value: SESSION_ID },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    await waitFor(() => {
      expect(voidSalaryPeriod).toHaveBeenCalledWith(PERIOD_ID, {
        reason: "Paid the wrong month",
        business_date: "2026-10-18",
        cash_sessions: [{ line_id: CASH_LINE_ID, cash_session_id: SESSION_ID }],
      });
    });
  });
});

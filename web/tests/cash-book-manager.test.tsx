import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  adjustCashSession,
  getCashSession,
  listCashSessions,
  listCashTransactions,
  openCashSession,
  reconcileCashSession,
} from "@/features/finance/client";
import { CashBookManager } from "@/features/finance/cash-book-manager";
import { OperationError } from "@/features/operations/client";
import type { CashSession } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  adjustCashSession: vi.fn(),
  getCashSession: vi.fn(),
  listCashSessions: vi.fn(),
  listCashTransactions: vi.fn(),
  openCashSession: vi.fn(),
  reconcileCashSession: vi.fn(),
}));

const SESSION_ID = "11111111-1111-4111-8111-111111111111";

function session(overrides: Partial<CashSession> = {}): CashSession {
  return {
    id: SESSION_ID,
    business_date: "2026-10-18",
    register_code: "FRONT",
    currency: "THB",
    opening_cash: "100.00",
    cash_sales: "0.00",
    cash_expenses: "0.00",
    manual_net: "0.00",
    expected_closing: "100.00",
    actual_closing: null,
    difference: null,
    difference_reason: "",
    status: "OPEN",
    opened_by_membership_id: "22222222-2222-4222-8222-222222222222",
    closed_by_membership_id: null,
    closed_at: null,
    created_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

function renderBook(canAdjust = false) {
  return render(
    <CashBookManager
      canAdjust={canAdjust}
      canOpen
      canReconcile
      currency="THB"
      defaultDate="2026-10-18"
    />,
  );
}

describe("cash book", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCashTransactions).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getCashSession).mockResolvedValue(session());
  });

  it("pages with the server cursor and hides adjustment without permission", async () => {
    vi.mocked(listCashSessions)
      .mockResolvedValueOnce({
        data: [session()],
        meta: { has_more: true, next_after: "cursor-2" },
      })
      .mockResolvedValueOnce({
        data: [
          session({
            id: "33333333-3333-4333-8333-333333333333",
            register_code: "SAFE",
          }),
        ],
        meta: { has_more: false, next_after: null },
      });
    renderBook(false);
    expect(await screen.findByRole("button", { name: "FRONT" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("button", { name: "SAFE" });
    expect(
      screen.queryByRole("button", { name: "FRONT" }),
    ).not.toBeInTheDocument();
    expect(vi.mocked(listCashSessions).mock.calls[1]?.[0]).toMatchObject({
      after: "cursor-2",
    });
    fireEvent.click(screen.getByRole("button", { name: "SAFE" }));
    expect(
      screen.queryByRole("button", { name: "Manual adjustment" }),
    ).not.toBeInTheDocument();
  });

  it("opens a session with an exact body and confirms a close without sending totals", async () => {
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [session()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(openCashSession).mockResolvedValue({
      session: session(),
      transactions: [],
    });
    vi.mocked(reconcileCashSession).mockResolvedValue({
      session: session({
        status: "CLOSED",
        actual_closing: "90.00",
        difference: "-10.00",
      }),
      transactions: [],
    });
    renderBook(true);
    const register = screen.getAllByRole("textbox", {
      name: "Register code",
    })[1];
    if (!register) throw new Error("Open-session register field is missing.");
    fireEvent.change(register, { target: { value: "front" } });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Opening cash (THB)" }),
      {
        target: { value: "50" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Open session" }));
    await waitFor(() => {
      expect(openCashSession).toHaveBeenCalledWith({
        register_code: "FRONT",
        business_date: "2026-10-18",
        opening_cash: "50.00",
      });
    });
    fireEvent.click(await screen.findByRole("button", { name: "FRONT" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Manual adjustment" }),
    );
    expect(
      screen.getByRole("heading", { name: "Manual adjustment" }),
    ).toBeVisible();
    fireEvent.click(
      await screen.findByRole("button", { name: "Reconcile and close" }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Actual closing" }), {
      target: { value: "90.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm close" }));
    expect(reconcileCashSession).not.toHaveBeenCalled();
    fireEvent.change(
      screen.getByRole("textbox", { name: /Difference reason/ }),
      {
        target: { value: "Drawer was short" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm close" }));
    await waitFor(() => {
      expect(reconcileCashSession).toHaveBeenCalledWith(SESSION_ID, {
        actual_closing: "90.00",
        difference_reason: "Drawer was short",
      });
    });
  });

  it("shows the server error", async () => {
    vi.mocked(listCashSessions).mockRejectedValue(
      new OperationError("The cash book is unavailable."),
    );
    renderBook();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The cash book is unavailable.",
    );
  });
});

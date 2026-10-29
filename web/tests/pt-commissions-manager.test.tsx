import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listCashSessions } from "@/features/finance/client";
import {
  getPtCommission,
  listPtCommissions,
  recordPtCommissionPayment,
} from "@/features/pt/client";
import { CommissionsManager } from "@/features/pt/commissions-manager";
import type { PTCommissionDetail } from "@/types/api";

vi.mock("@/features/pt/client", () => ({
  listPtCommissions: vi.fn(),
  getPtCommission: vi.fn(),
  recordPtCommissionPayment: vi.fn(),
}));

vi.mock("@/features/finance/client", () => ({
  listCashSessions: vi.fn(),
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const SALE = "33333333-3333-4333-8333-333333333333";
const SALE_2 = "77777777-7777-4777-8777-777777777777";
const CASH = "44444444-4444-4444-8444-444444444444";
const LATER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const COMMISSION = "55555555-5555-4555-8555-555555555555";
const SECOND = "66666666-6666-4666-8666-666666666666";
const PAYOUT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const KEY = /^[A-Za-z0-9._:-]{16,128}$/;
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  } as never;
}

function deferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function payout(
  extra: Record<string, unknown> = {},
): PTCommissionDetail["payments"][number] {
  return {
    id: PAYOUT,
    amount: "4.00",
    business_date: "2026-10-02",
    payment_method: "CASH",
    reference_number: "R-19",
    policy_note: "Weekly",
    status: "POSTED",
    expense_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    cash_session_id: CASH,
    cash_transaction_id: null,
    reversal_expense_id: null,
    reversal_cash_transaction_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    void_reason: "",
    created_by_membership_id: "payer",
    created_at: "2026-10-02T00:00:00Z",
    ...extra,
  } as PTCommissionDetail["payments"][number];
}

function commission(extra: Record<string, unknown> = {}): PTCommissionDetail {
  return {
    id: COMMISSION,
    pt_sale_id: SALE,
    trainer_staff_profile_id: TRAINER,
    gross_fee_snapshot: "100.00",
    commission_percent_snapshot: "10.00",
    commission_amount: "10.00",
    gym_share: "90.00",
    paid_amount: "0.00",
    outstanding_amount: "10.00",
    currency: "MMK",
    status: "ACCRUED",
    paid_at: null,
    created_by_membership_id: "creator",
    created_at: "2026-10-19T00:00:00Z",
    payments: [],
    ...extra,
  } as PTCommissionDetail;
}

function renderCommissions(
  props: Partial<{
    canPay: boolean;
    canPayOwn: boolean;
    membershipId: string;
    currency: string;
    today: string;
  }> = {},
) {
  return render(
    <CommissionsManager
      canPay={props.canPay ?? false}
      canPayOwn={props.canPayOwn ?? false}
      currency={props.currency ?? "MMK"}
      membershipId={props.membershipId ?? "payer"}
      today={props.today ?? "2026-10-19"}
    />,
  );
}

function expectDialogChrome(dialog: HTMLElement) {
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAttribute("aria-labelledby", "payout-title");
  expect(dialog).toHaveAttribute("aria-describedby", "payout-description");
  expect(dialog).toHaveAttribute("id", "payout-dialog");
  expect(dialog).toHaveClass("h-full", "sm:h-auto", "sm:max-w-2xl");
  expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
  expect(dialog.className).toContain("overflow-y-auto");
  expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
  expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveClass(
    "button-cancel",
  );
  expect(
    within(dialog).getByRole("button", { name: "Save payout" }),
  ).toHaveClass("button-edit");
  const backdrop = dialog.parentElement;
  if (!backdrop) throw new Error("Missing dialog backdrop.");
  expect(backdrop).toHaveAttribute("role", "presentation");
  expect(backdrop).toHaveClass("fixed", "inset-0", "sm:place-items-center");
}

function expectStillOpen(dialog: HTMLElement) {
  fireEvent.keyDown(dialog, { key: "Escape" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  fireEvent.mouseDown(dialog.parentElement ?? dialog);
  expect(screen.getByRole("dialog")).toBe(dialog);
}

async function openCommission(name = "Outstanding 10.00 MMK") {
  fireEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("region", { name: "Commission detail" });
}

async function openPayoutDialog() {
  await openCommission();
  fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
  return screen.findByRole("dialog", { name: "Record payout" });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listPtCommissions).mockResolvedValue(empty);
  vi.mocked(getPtCommission).mockResolvedValue(commission());
  vi.mocked(listCashSessions).mockResolvedValue(empty);
  vi.mocked(recordPtCommissionPayment).mockResolvedValue({
    commission_after: { id: COMMISSION },
  } as never);
});

describe("commission list and detail", () => {
  it("filters, pages, retries, and selects a commission", async () => {
    const first = commission();
    const second = commission({
      id: SECOND,
      pt_sale_id: SALE_2,
      outstanding_amount: "4.00",
      commission_amount: "8.00",
      status: "PARTIALLY_PAID",
    });
    let failed = true;
    vi.mocked(listPtCommissions).mockImplementation(async (query) => {
      if (failed) {
        failed = false;
        throw new Error("Commissions are unavailable.");
      }
      if (query?.after === "commission-2") return page([second]);
      if (query?.status === "PAID") return page([]);
      if (query?.status === "ACCRUED") return page([first]);
      return page([first], "commission-2");
    });
    renderCommissions({ canPay: true });

    const list = screen.getByRole("region", { name: "Commission list" });
    expect(await within(list).findByRole("alert")).toHaveTextContent(
      "Commissions are unavailable.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading commissions" }),
    );

    const row = await screen.findByRole("button", {
      name: "Outstanding 10.00 MMK",
    });
    expect(row).toHaveAttribute("aria-pressed", "false");
    expect(within(row).getByTitle(TRAINER)).toHaveTextContent("111111");
    expect(within(row).getByTitle(SALE)).toHaveTextContent("333333");
    expect(within(row).getByText("ACCRUED")).toBeInTheDocument();
    expect(within(row).getAllByText("10.00 MMK").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Select a commission to see its balance and payouts."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(
      await screen.findByRole("button", { name: "Outstanding 4.00 MMK" }),
    ).toBeInTheDocument();
    expect(listPtCommissions).toHaveBeenCalledWith({
      after: "commission-2",
      limit: "25",
    });
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByRole("button", { name: "Outstanding 10.00 MMK" });
    expect(listPtCommissions).toHaveBeenLastCalledWith({ limit: "25" });

    fireEvent.change(screen.getByLabelText("Commission status"), {
      target: { value: "PAID" },
    });
    expect(
      await screen.findByText("No commissions match."),
    ).toBeInTheDocument();
    expect(screen.getByText(/clearing the current filter/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filter" }));
    await screen.findByRole("button", { name: "Outstanding 10.00 MMK" });
    await waitFor(() =>
      expect(listPtCommissions).toHaveBeenLastCalledWith({ limit: "25" }),
    );

    fireEvent.change(screen.getByLabelText("Commission status"), {
      target: { value: "ACCRUED" },
    });
    await waitFor(() =>
      expect(listPtCommissions).toHaveBeenCalledWith({
        limit: "25",
        status: "ACCRUED",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Outstanding 10.00 MMK" }),
    );
    expect(
      await screen.findByRole("button", { name: "Outstanding 10.00 MMK" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Selected")).toBeInTheDocument();
    expect(getPtCommission).toHaveBeenCalledWith(COMMISSION);
  });

  it("shows the financial summary, identities, and payout history", async () => {
    const detail = commission({
      paid_amount: "4.00",
      outstanding_amount: "6.00",
      status: "PARTIALLY_PAID",
      payments: [payout()],
    });
    vi.mocked(listPtCommissions).mockResolvedValue(page([detail]));
    vi.mocked(getPtCommission).mockResolvedValue(detail);
    renderCommissions({ canPay: true });
    const panel = await openCommission("Outstanding 6.00 MMK");

    expect(within(panel).getByText("Financial summary")).toBeInTheDocument();
    expect(within(panel).getByText("10.00% of 100.00 MMK")).toBeInTheDocument();
    expect(within(panel).getByText("90.00 MMK")).toBeInTheDocument();
    expect(within(panel).getByText("6.00 MMK")).toBeInTheDocument();
    expect(within(panel).getByText("4.00 MMK")).toBeInTheDocument();
    expect(within(panel).getByText("PARTIALLY_PAID")).toBeInTheDocument();
    expect(within(panel).getByTitle(TRAINER)).toHaveTextContent("111111");
    expect(within(panel).getByTitle(SALE)).toHaveTextContent("333333");
    expect(within(panel).getByTitle(COMMISSION)).toHaveTextContent("555555");
    expect(within(panel).getByText("creator")).toBeInTheDocument();

    const history = within(panel).getByRole("list", { name: "Payout history" });
    expect(within(history).getByText("4.00 CASH")).toBeInTheDocument();
    expect(within(history).getByText("02-10-2026")).toBeInTheDocument();
    expect(within(history).getByText("POSTED")).toBeInTheDocument();
    expect(within(history).getByText("Reference R-19")).toBeInTheDocument();
    expect(within(history).getByTitle(CASH)).toHaveTextContent("444444");
    expect(
      within(panel).getByRole("button", { name: "Record payout" }),
    ).toHaveClass("button-edit");
  });

  it("ignores a stale detail response", async () => {
    const older = commission({ gym_share: "90.00" });
    const newer = commission({
      id: SECOND,
      pt_sale_id: SALE_2,
      gym_share: "70.00",
      outstanding_amount: "4.00",
    });
    vi.mocked(listPtCommissions).mockResolvedValue(page([older, newer]));
    const slow = deferred<PTCommissionDetail>();
    const fast = deferred<PTCommissionDetail>();
    vi.mocked(getPtCommission).mockImplementation((id: string) =>
      id === COMMISSION ? slow.promise : fast.promise,
    );
    renderCommissions();
    fireEvent.click(
      await screen.findByRole("button", { name: "Outstanding 10.00 MMK" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Outstanding 4.00 MMK" }),
    );
    await act(async () => {
      fast.resolve(newer);
      await fast.promise;
    });
    const detail = await screen.findByRole("region", {
      name: "Commission detail",
    });
    expect(await within(detail).findByText("70.00 MMK")).toBeInTheDocument();
    await act(async () => {
      slow.resolve(older);
      await slow.promise;
    });
    expect(within(detail).getByText("70.00 MMK")).toBeInTheDocument();
    expect(within(detail).queryByText("90.00 MMK")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Outstanding 4.00 MMK" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("retries a failed commission detail", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    vi.mocked(getPtCommission).mockRejectedValueOnce(
      new Error("Detail unavailable."),
    );
    renderCommissions({ canPay: true });
    fireEvent.click(
      await screen.findByRole("button", { name: "Outstanding 10.00 MMK" }),
    );
    const detail = await screen.findByRole("region", {
      name: "Commission detail",
    });
    expect(await within(detail).findByRole("alert")).toHaveTextContent(
      "Detail unavailable.",
    );
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();

    vi.mocked(getPtCommission).mockResolvedValueOnce(commission());
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading commission" }),
    );
    expect(
      await screen.findByRole("button", { name: "Record payout" }),
    ).toBeEnabled();
  });
});

describe("record payout dialog", () => {
  it("exposes dialog semantics and dismisses without saving", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    renderCommissions({ canPay: true });
    const dialog = await openPayoutDialog();
    expectDialogChrome(dialog);
    expect(dialog).toHaveTextContent("Outstanding balance");
    expect(dialog).toHaveTextContent("10.00");
    expect(dialog).toHaveTextContent("MMK");
    expect(dialog).toHaveTextContent(
      "Use the outstanding amount for a full payout, or a smaller amount for a partial payout. Currency MMK.",
    );
    expect(within(dialog).getByLabelText("Amount")).toHaveValue("10.00");
    expect(
      screen.getByRole("button", { name: "Record payout" }),
    ).toHaveAttribute("aria-controls", "payout-dialog");
    expect(
      screen.getByRole("button", { name: "Record payout" }),
    ).toHaveAttribute("aria-expanded", "true");
    fireEvent.mouseDown(within(dialog).getByLabelText("Amount"));
    expect(screen.getByRole("dialog", { name: "Record payout" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Record payout" }),
      ).toHaveFocus(),
    );
    expect(
      screen.getByRole("button", { name: "Record payout" }),
    ).not.toHaveAttribute("aria-controls");

    fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Close",
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
    fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
    const dismissed = await screen.findByRole("dialog");
    fireEvent.mouseDown(dismissed.parentElement ?? dismissed);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(recordPtCommissionPayment).not.toHaveBeenCalled();
  });

  it("locks the dialog while pending, then refreshes the selected commission", async () => {
    const refreshed = commission({
      paid_amount: "4.00",
      outstanding_amount: "6.00",
      status: "PARTIALLY_PAID",
      payments: [payout({ reference_number: "" })],
    });
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    vi.mocked(getPtCommission)
      .mockResolvedValueOnce(commission())
      .mockResolvedValueOnce(refreshed);
    renderCommissions({ canPay: true });
    const dialog = await openPayoutDialog();
    fireEvent.change(within(dialog).getByLabelText("Method"), {
      target: { value: "CARD" },
    });
    const pendingSave = deferred<{ commission_after: { id: string } }>();
    vi.mocked(recordPtCommissionPayment).mockReturnValue(
      pendingSave.promise as never,
    );
    const callsBefore = vi.mocked(listPtCommissions).mock.calls.length;
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );

    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save payout" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("Amount")).toBeDisabled();
    expectStillOpen(dialog);

    await act(async () => {
      pendingSave.resolve({ commission_after: { id: COMMISSION } });
      await pendingSave.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const detail = screen.getByRole("region", { name: "Commission detail" });
    expect(within(detail).getByRole("status")).toHaveTextContent(
      "Payout recorded.",
    );
    expect(
      await within(detail).findByText("PARTIALLY_PAID"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("6.00 MMK")).toBeInTheDocument();
    expect(within(detail).getByText("4.00 CASH")).toBeInTheDocument();
    expect(getPtCommission).toHaveBeenLastCalledWith(COMMISSION);
    await waitFor(() =>
      expect(vi.mocked(listPtCommissions).mock.calls.length).toBeGreaterThan(
        callsBefore,
      ),
    );
    expect(
      within(detail).getByRole("button", { name: "Record payout" }),
    ).toBeEnabled();
    expect(recordPtCommissionPayment).toHaveBeenCalledWith(
      COMMISSION,
      {
        amount: "10.00",
        business_date: "2026-10-19",
        payment_method: "CARD",
        reference_number: "",
        policy_note: "",
        cash_session_id: null,
      },
      expect.stringMatching(KEY),
    );
  });

  it("keeps payout errors in the dialog and rotates the idempotency key", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    vi.mocked(recordPtCommissionPayment).mockRejectedValue(
      new Error("Payout refused."),
    );
    renderCommissions({ canPay: true });
    const dialog = await openPayoutDialog();
    fireEvent.change(within(dialog).getByLabelText("Method"), {
      target: { value: "CARD" },
    });
    fireEvent.change(within(dialog).getByLabelText("Reference"), {
      target: { value: "R1" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent("Payout refused.");
    expect(dialog).toContainElement(failure);
    expect(
      within(
        screen.getByRole("region", { name: "Commission detail" }),
      ).queryByRole("alert"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Payout recorded.")).not.toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledTimes(2),
    );
    const firstKey = vi.mocked(recordPtCommissionPayment).mock.calls[0]?.[2];
    const secondKey = vi.mocked(recordPtCommissionPayment).mock.calls[1]?.[2];
    expect(firstKey).toMatch(KEY);
    expect(secondKey).toBe(firstKey);

    fireEvent.change(within(dialog).getByLabelText("Amount"), {
      target: { value: "4.00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledTimes(3),
    );
    const changedKey = vi.mocked(recordPtCommissionPayment).mock.calls[2]?.[2];
    expect(changedKey).not.toBe(firstKey);
    expect(vi.mocked(recordPtCommissionPayment).mock.calls[2]?.[1]).toEqual(
      expect.objectContaining({ amount: "4.00", reference_number: "R1" }),
    );

    fireEvent.change(within(dialog).getByLabelText("Amount"), {
      target: { value: "10.00" },
    });
    vi.mocked(recordPtCommissionPayment).mockResolvedValueOnce({
      commission_after: { id: COMMISSION },
    } as never);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const successKey = vi.mocked(recordPtCommissionPayment).mock.calls[3]?.[2];
    expect(successKey).not.toBe(changedKey);
    expect(
      within(
        screen.getByRole("region", { name: "Commission detail" }),
      ).getByRole("status"),
    ).toHaveTextContent("Payout recorded.");

    fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
    const again = await screen.findByRole("dialog", { name: "Record payout" });
    fireEvent.change(within(again).getByLabelText("Method"), {
      target: { value: "CARD" },
    });
    fireEvent.change(within(again).getByLabelText("Reference"), {
      target: { value: "R1" },
    });
    vi.mocked(recordPtCommissionPayment).mockResolvedValueOnce({
      commission_after: { id: COMMISSION },
    } as never);
    fireEvent.click(within(again).getByRole("button", { name: "Save payout" }));
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledTimes(5),
    );
    expect(vi.mocked(recordPtCommissionPayment).mock.calls[4]?.[2]).not.toBe(
      successKey,
    );
    expect(vi.mocked(recordPtCommissionPayment).mock.calls[4]?.[1]).toEqual({
      amount: "10.00",
      business_date: "2026-10-19",
      payment_method: "CARD",
      reference_number: "R1",
      policy_note: "",
      cash_session_id: null,
    });
  });
});

describe("payout permission and cash sessions", () => {
  it("shows record payout only for a different payer on an open balance", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));

    renderCommissions({ canPay: false });
    await openCommission();
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/different person/)).not.toBeInTheDocument();
    cleanup();

    renderCommissions({ canPay: true, membershipId: "creator" });
    await openCommission();
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("A different person must record this payout."),
    ).toBeInTheDocument();
    cleanup();

    renderCommissions({
      canPay: true,
      canPayOwn: true,
      membershipId: "creator",
    });
    await openCommission();
    expect(
      screen.getByRole("button", { name: "Record payout" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/different person/)).not.toBeInTheDocument();
    cleanup();

    vi.mocked(getPtCommission).mockResolvedValue(
      commission({
        status: "PAID",
        outstanding_amount: "0.00",
        paid_amount: "10.00",
      }),
    );
    renderCommissions({ canPay: true });
    await openCommission();
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/different person/)).not.toBeInTheDocument();
    cleanup();

    vi.mocked(getPtCommission).mockResolvedValue(
      commission({ status: "VOID", outstanding_amount: "10.00" }),
    );
    renderCommissions({ canPay: true });
    await openCommission();
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();
    cleanup();

    vi.mocked(getPtCommission).mockResolvedValue(
      commission({ status: "PARTIALLY_PAID", outstanding_amount: "0.00" }),
    );
    renderCommissions({ canPay: true });
    await openCommission();
    expect(
      screen.queryByRole("button", { name: "Record payout" }),
    ).not.toBeInTheDocument();
    cleanup();

    vi.mocked(getPtCommission).mockResolvedValue(
      commission({
        status: "PARTIALLY_PAID",
        outstanding_amount: "6.00",
        paid_amount: "4.00",
      }),
    );
    renderCommissions({ canPay: true });
    await openCommission();
    expect(screen.getByRole("button", { name: "Record payout" })).toBeEnabled();
  });

  it("requires an open cash session and reloads sessions for the business date", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      if (query?.business_date === "2026-10-03") {
        return {
          data: [
            {
              id: LATER,
              status: "OPEN",
              currency: "MMK",
              register_code: "BACK",
              business_date: "2026-10-03",
            },
          ],
          meta: { has_more: false, next_after: null },
        } as never;
      }
      return {
        data: [
          {
            id: CASH,
            status: "OPEN",
            currency: "MMK",
            register_code: "FRONT",
            business_date: "2026-10-19",
          },
        ],
        meta: { has_more: false, next_after: null },
      } as never;
    });
    renderCommissions({ canPay: true });
    const dialog = await openPayoutDialog();
    await within(dialog).findByRole("option", { name: /FRONT/ });
    fireEvent.change(within(dialog).getByLabelText("Reference"), {
      target: { value: "REF-1" },
    });
    fireEvent.change(within(dialog).getByLabelText("Policy note"), {
      target: { value: "Weekly" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Select an open cash session.",
    );
    expect(dialog).toContainElement(within(dialog).getByRole("alert"));
    expect(recordPtCommissionPayment).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Business date"), {
      target: { value: "2026-10-03" },
    });
    await within(dialog).findByRole("option", { name: /BACK/ });
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({
        business_date: "2026-10-03",
        limit: "100",
        status: "OPEN",
      }),
    );
    fireEvent.change(
      within(dialog).getByLabelText("Cash session when the method is cash"),
      { target: { value: LATER } },
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledWith(
        COMMISSION,
        {
          amount: "10.00",
          business_date: "2026-10-03",
          payment_method: "CASH",
          reference_number: "REF-1",
          policy_note: "Weekly",
          cash_session_id: LATER,
        },
        expect.stringMatching(KEY),
      ),
    );
  });

  it("disables cash payout when sessions fail and sends null for other methods", async () => {
    vi.mocked(listPtCommissions).mockResolvedValue(page([commission()]));
    vi.mocked(listCashSessions).mockRejectedValue(
      new Error("Sessions unavailable."),
    );
    renderCommissions({ canPay: true });
    const dialog = await openPayoutDialog();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Sessions unavailable.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save payout" }),
    ).toBeDisabled();
    expect(recordPtCommissionPayment).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Method"), {
      target: { value: "BANK_TRANSFER" },
    });
    expect(
      within(dialog).getByText(/no cash session is sent/),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByLabelText("Cash session when the method is cash"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Save payout" }),
    ).toBeEnabled();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save payout" }),
    );
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledWith(
        COMMISSION,
        {
          amount: "10.00",
          business_date: "2026-10-19",
          payment_method: "BANK_TRANSFER",
          reference_number: "",
          policy_note: "",
          cash_session_id: null,
        },
        expect.stringMatching(KEY),
      ),
    );
  });
});

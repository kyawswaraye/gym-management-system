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

import { listCashSessions, listReceipts } from "@/features/finance/client";
import { listMembers } from "@/features/operations/client";
import {
  approvePtSale,
  cancelPtSale,
  createPtSale,
  getPtCommission,
  getPtSale,
  listPtCommissions,
  listPtSales,
  listServicePackages,
  listTrainers,
  rejectPtSale,
  submitPtSale,
  updatePtSale,
  voidPtSale,
} from "@/features/pt/client";
import { SalesManager } from "@/features/pt/sales-manager";
import type { PTSale } from "@/types/api";

vi.mock("@/features/pt/client", () => ({
  listTrainers: vi.fn(),
  listServicePackages: vi.fn(),
  listPtSales: vi.fn(),
  getPtSale: vi.fn(),
  createPtSale: vi.fn(),
  updatePtSale: vi.fn(),
  submitPtSale: vi.fn(),
  approvePtSale: vi.fn(),
  rejectPtSale: vi.fn(),
  cancelPtSale: vi.fn(),
  voidPtSale: vi.fn(),
  listPtCommissions: vi.fn(),
  getPtCommission: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {
    readonly code: string;
    constructor(
      message: string,
      _fields: Record<string, string> = {},
      code = "",
    ) {
      super(message);
      this.code = code;
    }
  },
  listMembers: vi.fn(),
}));

vi.mock("@/features/finance/client", () => ({
  listCashSessions: vi.fn(),
  listReceipts: vi.fn(),
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const MEMBER = "22222222-2222-4222-8222-222222222222";
const SALE = "33333333-3333-4333-8333-333333333333";
const PACKAGE = "44444444-4444-4444-8444-444444444444";
const CASH = "55555555-5555-4555-8555-555555555555";
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

function sale(status: string, extra: Record<string, unknown> = {}): PTSale {
  return {
    id: SALE,
    member_id: MEMBER,
    trainer_staff_profile_id: TRAINER,
    service_date: "2026-10-19",
    description: "Pack",
    gross_fee: "100.00",
    currency: "MMK",
    commission_percent_snapshot: "10.00",
    commission_amount: "10.00",
    gym_share: "90.00",
    paid_amount: "0.00",
    outstanding_amount: "100.00",
    status,
    payment_id: null,
    payment: null,
    service_package_id: null,
    package_price_snapshot: null,
    member_service_package_id: null,
    submitted_at: null,
    submitted_by_membership_id: status === "DRAFT" ? null : "submitter",
    approved_at: null,
    approved_by_membership_id: null,
    rejected_at: null,
    rejected_by_membership_id: null,
    rejection_reason: "",
    posted_at: null,
    cancelled_at: null,
    cancelled_by_membership_id: null,
    voided_at: null,
    voided_by_membership_id: null,
    reversal_pt_sale_id: null,
    reason: "",
    created_by_membership_id: "creator",
    updated_by_membership_id: "creator",
    created_at: "2026-10-19T00:00:00Z",
    updated_at: "2026-10-19T00:00:00Z",
    ...extra,
  } as PTSale;
}

function renderSales(
  props: Partial<{
    canCreate: boolean;
    canUpdate: boolean;
    canSubmit: boolean;
    canApprove: boolean;
    canViewReceipts: boolean;
    canVoid: boolean;
    membershipId: string;
  }> = {},
) {
  return render(
    <SalesManager
      canApprove={props.canApprove ?? false}
      canCreate={props.canCreate ?? false}
      canSubmit={props.canSubmit ?? false}
      canUpdate={props.canUpdate ?? false}
      canViewReceipts={props.canViewReceipts ?? false}
      canVoid={props.canVoid ?? false}
      currency="MMK"
      membershipId={props.membershipId ?? "approver"}
      today="2026-10-19"
    />,
  );
}

function expectDialogChrome(
  dialog: HTMLElement,
  {
    title,
    titleId,
    descriptionId,
    dialogId,
    submit,
    submitClass,
  }: {
    title: string;
    titleId: string;
    descriptionId: string;
    dialogId: string;
    submit: string;
    submitClass: string;
  },
) {
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAttribute("aria-labelledby", titleId);
  expect(dialog).toHaveAttribute("aria-describedby", descriptionId);
  expect(dialog).toHaveAttribute("id", dialogId);
  expect(
    within(dialog).getByRole("heading", { name: title }),
  ).toBeInTheDocument();
  expect(dialog).toHaveClass("h-full", "sm:h-auto", "sm:max-w-2xl");
  expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
  expect(dialog.className).toContain("overflow-y-auto");
  expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
  expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveClass(
    "button-cancel",
  );
  expect(within(dialog).getByRole("button", { name: submit })).toHaveClass(
    submitClass,
  );
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

async function openSaleRow() {
  fireEvent.click(
    await screen.findByRole("button", { name: /Pack|100.00 MMK/ }),
  );
  const detail = await screen.findByRole("region", { name: "Sale detail" });
  await within(detail).findByText("Gross fee");
  return detail;
}

async function chooseMember(scope: HTMLElement, action = "Search members") {
  vi.mocked(listMembers).mockResolvedValue(
    page([
      {
        id: MEMBER,
        member_number: "M-1",
        first_name: "Grace",
        last_name: "Hopper",
      },
    ]),
  );
  fireEvent.click(within(scope).getByRole("button", { name: action }));
  fireEvent.click(
    await within(scope).findByRole("button", { name: "Grace Hopper (M-1)" }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listTrainers).mockResolvedValue(empty);
  vi.mocked(listServicePackages).mockResolvedValue(empty);
  vi.mocked(listPtSales).mockResolvedValue(empty);
  vi.mocked(listPtCommissions).mockResolvedValue(empty);
  vi.mocked(listMembers).mockResolvedValue(empty);
  vi.mocked(listCashSessions).mockResolvedValue(empty);
  vi.mocked(listReceipts).mockResolvedValue(empty);
});

describe("PT sales list and detail", () => {
  it("filters, pages, and retries the sale list", async () => {
    let failed = true;
    const later = sale("POSTED", {
      id: PACKAGE,
      description: "Later",
      gross_fee: "40.00",
    });
    vi.mocked(listPtSales).mockImplementation(async (query) => {
      if (failed) {
        failed = false;
        throw new Error("Sales are unavailable.");
      }
      if (query?.after === "sale-2") return page([later]);
      if (query?.status === "POSTED") return page([later]);
      if (query?.member_id === MEMBER) return page([sale("DRAFT")]);
      return page([sale("DRAFT")], "sale-2");
    });
    renderSales({ canCreate: true });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sales are unavailable.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading sales" }),
    );
    const row = await screen.findByRole("button", { name: /100.00 MMK/ });
    expect(row).toHaveAttribute("aria-pressed", "false");
    expect(within(row).getByTitle(MEMBER)).toHaveTextContent("222222");
    expect(within(row).getByTitle(TRAINER)).toHaveTextContent("111111");
    expect(screen.getByRole("button", { name: "Create draft" })).toHaveClass(
      "button-create",
    );

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(
      await screen.findByRole("button", { name: /Later/ }),
    ).toBeInTheDocument();
    expect(listPtSales).toHaveBeenCalledWith(
      expect.objectContaining({ after: "sale-2", limit: "25" }),
    );

    fireEvent.change(screen.getByLabelText("Sale status"), {
      target: { value: "POSTED" },
    });
    await waitFor(() =>
      expect(listPtSales).toHaveBeenCalledWith(
        expect.objectContaining({ status: "POSTED", limit: "25" }),
      ),
    );
    await chooseMember(
      screen.getByRole("region", { name: "Sales" }),
      "Search sale members",
    );
    await waitFor(() =>
      expect(listPtSales).toHaveBeenCalledWith(
        expect.objectContaining({ member_id: MEMBER, status: "POSTED" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      const latest = vi.mocked(listPtSales).mock.calls.at(-1)?.[0];
      expect(latest).not.toHaveProperty("status");
      expect(latest).not.toHaveProperty("member_id");
    });
    expect(screen.getByLabelText("Sale status")).toHaveValue("");
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty list and a selected sale with its financial facts", async () => {
    renderSales();
    expect(await screen.findByText("No PT sales match.")).toBeInTheDocument();
    expect(
      screen.getByText(/Select a sale to see its fee/),
    ).toBeInTheDocument();
    cleanup();

    const posted = sale("POSTED", {
      service_package_id: PACKAGE,
      package_price_snapshot: "80.00",
      payment: {
        method: "CARD",
        amount: "100.00",
        currency: "MMK",
        business_date: "2026-10-19",
        reference_number: "REF-9",
        status: "POSTED",
      },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([posted]));
    vi.mocked(getPtSale).mockResolvedValue(posted);
    vi.mocked(listPtCommissions).mockResolvedValue(
      page([
        {
          id: CASH,
          pt_sale_id: SALE,
          trainer_staff_profile_id: TRAINER,
        },
      ]),
    );
    vi.mocked(getPtCommission).mockResolvedValue({
      id: CASH,
      pt_sale_id: SALE,
      trainer_staff_profile_id: TRAINER,
      gross_fee_snapshot: "100.00",
      commission_percent_snapshot: "10.00",
      commission_amount: "10.00",
      gym_share: "90.00",
      paid_amount: "4.00",
      outstanding_amount: "6.00",
      currency: "MMK",
      status: "PARTIAL",
      paid_at: null,
      created_by_membership_id: "creator",
      created_at: "2026-10-19T00:00:00Z",
      payments: [
        {
          id: PACKAGE,
          amount: "4.00",
          business_date: "2026-10-19",
          payment_method: "CARD",
          reference_number: "",
          policy_note: "",
          status: "POSTED",
          expense_id: SALE,
          cash_session_id: null,
          cash_transaction_id: null,
          reversal_expense_id: null,
          reversal_cash_transaction_id: null,
          voided_at: null,
          voided_by_membership_id: null,
          void_reason: "",
          created_by_membership_id: "creator",
          created_at: "2026-10-19T00:00:00Z",
        },
      ],
    } as never);
    renderSales();
    const row = await screen.findByRole("button", { name: /payment CARD/ });
    expect(row).toHaveTextContent("Package");
    expect(row).toHaveTextContent("CARD");
    fireEvent.click(row);
    const detail = await screen.findByRole("region", { name: "Sale detail" });
    expect(row).toHaveAttribute("aria-pressed", "true");
    expect(row).toHaveTextContent("Selected");
    expect(within(detail).getByText("Gross fee")).toBeInTheDocument();
    expect(within(detail).getAllByText("100.00 MMK").length).toBeGreaterThan(0);
    expect(within(detail).getByText("Gym share")).toBeInTheDocument();
    expect(within(detail).getByText("90.00")).toBeInTheDocument();
    expect(within(detail).getByText("Outstanding")).toBeInTheDocument();
    expect(within(detail).getByText("80.00")).toBeInTheDocument();
    expect(within(detail).getByText("REF-9")).toBeInTheDocument();
    expect(
      await within(detail).findByText("Commission outstanding"),
    ).toBeInTheDocument();
    expect(within(detail).getAllByText("4.00").length).toBeGreaterThan(0);
    expect(
      within(detail).getByLabelText("Commission payouts"),
    ).toHaveTextContent("CARD");
  });

  it("retries a failed sale detail load", async () => {
    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockRejectedValueOnce(new Error("Sale missing."));
    renderSales();
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    const detail = await screen.findByRole("region", { name: "Sale detail" });
    expect(await within(detail).findByRole("alert")).toHaveTextContent(
      "Sale missing.",
    );
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT"));
    fireEvent.click(
      within(detail).getByRole("button", { name: "Retry loading sale" }),
    );
    expect(await within(detail).findByText("Gym share")).toBeInTheDocument();
  });
});

describe("PT sales permissions", () => {
  it("hides unauthorized actions and enforces maker-checker approval", async () => {
    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT"));
    const view = renderSales();
    await openSaleRow();
    expect(screen.queryByRole("button", { name: "Create draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Submit sale" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve sale" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject sale" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Void sale" })).toBeNull();

    view.rerender(
      <SalesManager
        canApprove={false}
        canCreate
        canSubmit
        canUpdate
        canVoid={false}
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    expect(screen.getByRole("button", { name: "Create draft" })).toHaveClass(
      "button-create",
    );
    expect(screen.getByRole("button", { name: "Edit draft" })).toHaveClass(
      "button-edit",
    );
    expect(screen.getByRole("button", { name: "Cancel draft" })).toHaveClass(
      "button-warn",
    );
    expect(screen.getByRole("button", { name: "Submit sale" })).toBeEnabled();

    vi.mocked(getPtSale).mockResolvedValue(sale("PENDING_APPROVAL"));
    vi.mocked(listPtSales).mockResolvedValue(page([sale("PENDING_APPROVAL")]));
    view.rerender(
      <SalesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="MMK"
        membershipId="creator"
        today="2026-10-19"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /100.00 MMK/ }));
    expect(
      await screen.findByText(
        "A different person must approve or reject this sale.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve sale" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject sale" })).toBeNull();

    view.rerender(
      <SalesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="MMK"
        membershipId="submitter"
        today="2026-10-19"
      />,
    );
    expect(screen.queryByRole("button", { name: "Approve sale" })).toBeNull();

    view.rerender(
      <SalesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    expect(
      await screen.findByRole("button", { name: "Approve sale" }),
    ).toHaveClass("button-success");
    expect(screen.getByRole("button", { name: "Reject sale" })).toHaveClass(
      "button-warn",
    );

    vi.mocked(getPtSale).mockResolvedValue(sale("POSTED"));
    vi.mocked(listPtSales).mockResolvedValue(page([sale("POSTED")]));
    fireEvent.click(screen.getByRole("button", { name: /100.00 MMK/ }));
    expect(
      await screen.findByRole("button", { name: "Void sale" }),
    ).toHaveClass("button-delete");
    view.rerender(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    expect(screen.queryByRole("button", { name: "Void sale" })).toBeNull();
  });
});

describe("create and edit draft dialogs", () => {
  async function openCreate() {
    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
    renderSales({ canCreate: true });
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    const dialog = await screen.findByRole("dialog", { name: "Create draft" });
    await within(dialog).findByRole("option", { name: /Ada/ });
    return dialog;
  }

  async function fillDraft(dialog: HTMLElement, description = "Pack") {
    await chooseMember(dialog);
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.change(within(dialog).getByLabelText("Description"), {
      target: { value: description },
    });
    fireEvent.change(within(dialog).getByLabelText("Gross fee"), {
      target: { value: "100.00" },
    });
  }

  it("closes from Cancel, Close, Escape, and the backdrop", async () => {
    const dialog = await openCreate();
    expectDialogChrome(dialog, {
      title: "Create draft",
      titleId: "sale-form-title",
      descriptionId: "sale-form-description",
      dialogId: "sale-draft-dialog",
      submit: "Save draft",
      submitClass: "button-edit",
    });
    expect(
      screen.getByRole("button", { name: "Create draft" }),
    ).toHaveAttribute("aria-controls", "sale-draft-dialog");
    fireEvent.mouseDown(within(dialog).getByLabelText("Description"));
    expect(screen.getByRole("dialog", { name: "Create draft" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const reopened = await screen.findByRole("button", {
      name: "Create draft",
    });
    fireEvent.click(reopened);
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Close",
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    const dismissed = await screen.findByRole("dialog");
    fireEvent.mouseDown(dismissed.parentElement ?? dismissed);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps validation and request failures inside the dialog", async () => {
    const dialog = await openCreate();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    const memberError = within(dialog).getByRole("alert");
    expect(memberError).toHaveTextContent("Select an active member.");
    expect(dialog).toContainElement(memberError);
    expect(createPtSale).not.toHaveBeenCalled();

    await fillDraft(dialog);
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: "" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Select a trainer.",
    );
    expect(createPtSale).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    vi.mocked(createPtSale).mockRejectedValueOnce(new Error("Draft refused."));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent("Draft refused.");
    expect(dialog).toContainElement(failure);
    expect(screen.getByRole("dialog", { name: "Create draft" })).toBe(dialog);
  });

  it("locks the create dialog while the save is pending and then shows success", async () => {
    const dialog = await openCreate();
    await fillDraft(dialog);
    const pendingSave = deferred<PTSale>();
    vi.mocked(createPtSale).mockReturnValue(pendingSave.promise);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save draft" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("Trainer")).toBeDisabled();
    expectStillOpen(dialog);

    await act(async () => {
      pendingSave.resolve(sale("DRAFT"));
      await pendingSave.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const detail = screen.getByRole("region", { name: "Sale detail" });
    expect(within(detail).getByRole("status")).toHaveTextContent(
      "Draft created.",
    );
    expect(createPtSale).toHaveBeenCalledWith(
      expect.objectContaining({
        member_id: MEMBER,
        trainer_staff_profile_id: TRAINER,
        description: "Pack",
        gross_fee: "100.00",
        service_package_id: null,
      }),
      expect.stringMatching(KEY),
    );
    expect(vi.mocked(createPtSale).mock.calls[0]?.[0]).not.toHaveProperty(
      "payment",
    );
  });

  it("reuses the draft idempotency key until the payload changes", async () => {
    const dialog = await openCreate();
    await fillDraft(dialog);
    vi.mocked(createPtSale).mockRejectedValueOnce(new Error("Try again."));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    await within(dialog).findByRole("alert");
    vi.mocked(createPtSale).mockResolvedValueOnce(sale("DRAFT"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createPtSale).toHaveBeenCalledTimes(2));
    const first = vi.mocked(createPtSale).mock.calls[0]?.[1];
    const second = vi.mocked(createPtSale).mock.calls[1]?.[1];
    expect(first).toMatch(KEY);
    expect(second).toBe(first);

    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    const again = await screen.findByRole("dialog", { name: "Create draft" });
    await fillDraft(again, "Updated pack");
    vi.mocked(createPtSale).mockResolvedValueOnce(sale("DRAFT"));
    fireEvent.click(within(again).getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createPtSale).toHaveBeenCalledTimes(3));
    expect(vi.mocked(createPtSale).mock.calls[2]?.[1]).not.toBe(first);
  });

  it("edits a draft from a dialog and reports the failure in that dialog", async () => {
    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT"));
    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
    renderSales({ canUpdate: true });
    await openSaleRow();
    fireEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit draft" });
    expectDialogChrome(dialog, {
      title: "Edit draft",
      titleId: "sale-form-title",
      descriptionId: "sale-form-description",
      dialogId: "sale-draft-dialog",
      submit: "Save draft",
      submitClass: "button-edit",
    });
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit draft" }));
    const editing = await screen.findByRole("dialog", { name: "Edit draft" });
    await within(editing).findByRole("option", { name: /Ada/ });
    fireEvent.change(within(editing).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.change(within(editing).getByLabelText("Description"), {
      target: { value: "Updated" },
    });
    fireEvent.change(within(editing).getByLabelText("Payment draft"), {
      target: { value: "clear" },
    });
    vi.mocked(updatePtSale).mockRejectedValueOnce(new Error("Edit refused."));
    fireEvent.click(
      within(editing).getByRole("button", { name: "Save draft" }),
    );
    expect(await within(editing).findByRole("alert")).toHaveTextContent(
      "Edit refused.",
    );
    expect(screen.getByRole("dialog")).toBe(editing);

    const pendingSave = deferred<PTSale>();
    vi.mocked(updatePtSale).mockReturnValue(pendingSave.promise);
    fireEvent.click(
      within(editing).getByRole("button", { name: "Save draft" }),
    );
    expect(editing).toHaveAttribute("aria-busy", "true");
    expectStillOpen(editing);
    await act(async () => {
      pendingSave.resolve(sale("DRAFT", { description: "Updated" }));
      await pendingSave.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(updatePtSale).toHaveBeenLastCalledWith(
      SALE,
      expect.objectContaining({
        member_id: MEMBER,
        description: "Updated",
        payment: null,
      }),
      expect.stringMatching(KEY),
    );
    expect(
      within(screen.getByRole("region", { name: "Sale detail" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("Draft updated.");
  });
});

describe("submit sale", () => {
  it("submits directly and keeps success, errors, and the idempotency key in the detail", async () => {
    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT"));
    renderSales({ canSubmit: true });
    const detail = await openSaleRow();
    vi.mocked(submitPtSale).mockRejectedValueOnce(new Error("Submit refused."));
    fireEvent.click(
      within(detail).getByRole("button", { name: "Submit sale" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await within(detail).findByRole("alert")).toHaveTextContent(
      "Submit refused.",
    );
    const first = vi.mocked(submitPtSale).mock.calls[0]?.[1];
    vi.mocked(submitPtSale).mockResolvedValue(sale("PENDING_APPROVAL"));
    fireEvent.click(
      within(detail).getByRole("button", { name: "Submit sale" }),
    );
    await waitFor(() => expect(submitPtSale).toHaveBeenCalledTimes(2));
    expect(vi.mocked(submitPtSale).mock.calls[1]?.[1]).toBe(first);
    expect(
      await within(detail).findByText("Sale submitted."),
    ).toBeInTheDocument();
    expect(getPtSale).toHaveBeenCalledWith(SALE);
  });
});

describe("approve, reject, cancel, and void dialogs", () => {
  it("approves only with a usable cash session and omits one for non-cash", async () => {
    const cashSale = sale("PENDING_APPROVAL", {
      payment: { method: "CASH", business_date: "2026-09-30" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([cashSale]));
    vi.mocked(getPtSale).mockResolvedValue(cashSale);
    vi.mocked(listCashSessions).mockResolvedValue({
      data: [
        {
          id: CASH,
          status: "OPEN",
          currency: "MMK",
          register_code: "FRONT",
          business_date: "2026-09-30",
        },
      ],
      meta: { has_more: false, next_after: null },
    } as never);
    const view = renderSales({ canApprove: true });
    await openSaleRow();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve sale" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Approve sale" });
    expectDialogChrome(dialog, {
      title: "Approve sale",
      titleId: "approve-title",
      descriptionId: "approve-description",
      dialogId: "approve-sale-dialog",
      submit: "Confirm approval",
      submitClass: "button-success",
    });
    expect(dialog).toHaveTextContent("payment business date 30-09-2026");
    await within(dialog).findByRole("option", { name: /FRONT/ });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm approval" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Select an open cash session.",
    );
    expect(approvePtSale).not.toHaveBeenCalled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    vi.mocked(listCashSessions).mockRejectedValueOnce(
      new Error("cursor repeated"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve sale" }));
    const blocked = await screen.findByRole("dialog", { name: "Approve sale" });
    expect(await within(blocked).findByRole("alert")).toHaveTextContent(
      "cursor repeated",
    );
    expect(
      within(blocked).getByRole("button", { name: "Confirm approval" }),
    ).toBeDisabled();
    expect(approvePtSale).not.toHaveBeenCalled();
    fireEvent.click(within(blocked).getByRole("button", { name: "Cancel" }));

    vi.mocked(listCashSessions).mockResolvedValue({
      data: [
        {
          id: CASH,
          status: "OPEN",
          currency: "MMK",
          register_code: "FRONT",
          business_date: "2026-09-30",
        },
      ],
      meta: { has_more: false, next_after: null },
    } as never);
    fireEvent.click(screen.getByRole("button", { name: "Approve sale" }));
    const ready = await screen.findByRole("dialog", { name: "Approve sale" });
    await within(ready).findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      within(ready).getByLabelText("Cash session for the payment"),
      {
        target: { value: CASH },
      },
    );
    const pendingApproval = deferred<never>();
    vi.mocked(approvePtSale).mockReturnValue(pendingApproval.promise as never);
    fireEvent.click(
      within(ready).getByRole("button", { name: "Confirm approval" }),
    );
    expect(ready).toHaveAttribute("aria-busy", "true");
    expectStillOpen(ready);
    await act(async () => {
      pendingApproval.resolve({} as never);
      await pendingApproval.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(approvePtSale).toHaveBeenCalledWith(
      SALE,
      { cash_session_id: CASH },
      expect.stringMatching(KEY),
    );
    expect(
      within(screen.getByRole("region", { name: "Sale detail" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("Sale approved.");
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({
        status: "OPEN",
        business_date: "2026-09-30",
        limit: "100",
      }),
    );

    const cardSale = sale("PENDING_APPROVAL", {
      payment: { method: "CARD", business_date: "2026-09-30" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([cardSale]));
    vi.mocked(getPtSale).mockResolvedValue(cardSale);
    vi.mocked(listCashSessions).mockClear();
    view.rerender(
      <SalesManager
        canApprove
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve sale" }),
    );
    const cardDialog = await screen.findByRole("dialog", {
      name: "Approve sale",
    });
    expect(cardDialog).toHaveTextContent("no cash session is sent");
    expect(
      within(cardDialog).queryByLabelText("Cash session for the payment"),
    ).not.toBeInTheDocument();
    vi.mocked(approvePtSale).mockResolvedValue({} as never);
    fireEvent.click(
      within(cardDialog).getByRole("button", { name: "Confirm approval" }),
    );
    await waitFor(() =>
      expect(approvePtSale).toHaveBeenLastCalledWith(
        SALE,
        {},
        expect.stringMatching(KEY),
      ),
    );
    expect(listCashSessions).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    expect(listReceipts).not.toHaveBeenCalled();
  });

  it("rejects and cancels from dialogs without losing the reason", async () => {
    vi.mocked(listPtSales).mockResolvedValue(page([sale("PENDING_APPROVAL")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("PENDING_APPROVAL"));
    vi.mocked(rejectPtSale).mockResolvedValue(sale("REJECTED"));
    const view = renderSales({ canApprove: true, canUpdate: true });
    await openSaleRow();
    fireEvent.click(await screen.findByRole("button", { name: "Reject sale" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject sale" });
    expectDialogChrome(dialog, {
      title: "Reject sale",
      titleId: "reject-title",
      descriptionId: "reject-description",
      dialogId: "reject-sale-dialog",
      submit: "Confirm rejection",
      submitClass: "button-warn",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm rejection" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Enter a rejection reason.",
    );
    expect(rejectPtSale).not.toHaveBeenCalled();
    fireEvent.mouseDown(
      within(dialog).getByRole("textbox", { name: "Rejection reason" }),
    );
    expect(screen.getByRole("dialog")).toBe(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reject sale" }));
    const rejecting = await screen.findByRole("dialog", {
      name: "Reject sale",
    });
    fireEvent.change(
      within(rejecting).getByRole("textbox", { name: "Rejection reason" }),
      { target: { value: "Wrong fee" } },
    );
    const pendingReject = deferred<PTSale>();
    vi.mocked(rejectPtSale).mockReturnValue(pendingReject.promise);
    fireEvent.click(
      within(rejecting).getByRole("button", { name: "Confirm rejection" }),
    );
    expect(rejecting).toHaveAttribute("aria-busy", "true");
    expectStillOpen(rejecting);
    await act(async () => {
      pendingReject.resolve(sale("REJECTED"));
      await pendingReject.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(rejectPtSale).toHaveBeenCalledWith(
      SALE,
      { rejection_reason: "Wrong fee" },
      expect.stringMatching(KEY),
    );
    expect(await screen.findByText("Sale rejected.")).toBeInTheDocument();

    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT"));
    view.rerender(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate
        canVoid={false}
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel draft" }),
    );
    const cancelling = await screen.findByRole("dialog", {
      name: "Cancel draft",
    });
    expectDialogChrome(cancelling, {
      title: "Cancel draft",
      titleId: "cancel-sale-title",
      descriptionId: "cancel-sale-description",
      dialogId: "cancel-sale-dialog",
      submit: "Confirm cancellation",
      submitClass: "button-cancel",
    });
    fireEvent.click(
      within(cancelling).getByRole("button", { name: "Confirm cancellation" }),
    );
    expect(within(cancelling).getByRole("alert")).toHaveTextContent(
      "Enter a cancellation reason.",
    );
    expect(cancelPtSale).not.toHaveBeenCalled();
    fireEvent.mouseDown(cancelling.parentElement ?? cancelling);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel draft" }));
    const again = await screen.findByRole("dialog", { name: "Cancel draft" });
    fireEvent.change(
      within(again).getByRole("textbox", { name: "Cancellation reason" }),
      { target: { value: "Not needed" } },
    );
    vi.mocked(cancelPtSale).mockResolvedValue(sale("CANCELLED"));
    fireEvent.click(
      within(again).getByRole("button", { name: "Confirm cancellation" }),
    );
    await waitFor(() =>
      expect(cancelPtSale).toHaveBeenCalledWith(
        SALE,
        { reason: "Not needed" },
        expect.stringMatching(KEY),
      ),
    );
    expect(await screen.findByText("Draft cancelled.")).toBeInTheDocument();
  });

  it("blocks an unresolved or sessionless void and confirms a complete one", async () => {
    const posted = sale("POSTED", { payment: { method: "CASH" } });
    vi.mocked(listPtSales).mockResolvedValue(page([posted]));
    vi.mocked(getPtSale).mockResolvedValue(posted);
    vi.mocked(listPtCommissions).mockImplementation(async (query) =>
      query?.after === "cursor-again"
        ? page([], "cursor-again")
        : page(
            [
              {
                id: PACKAGE,
                pt_sale_id: TRAINER,
                trainer_staff_profile_id: TRAINER,
              },
            ],
            "cursor-again",
          ),
    );
    renderSales({ canVoid: true });
    await openSaleRow();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "cursor repeated",
    );
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    const unresolved = await screen.findByRole("dialog", { name: "Void sale" });
    expectDialogChrome(unresolved, {
      title: "Void sale",
      titleId: "void-sale-title",
      descriptionId: "void-sale-description",
      dialogId: "void-sale-dialog",
      submit: "Confirm void",
      submitClass: "button-delete",
    });
    expect(unresolved).toHaveTextContent(/was not sent/);
    expect(
      within(unresolved).getByRole("button", { name: "Confirm void" }),
    ).toBeDisabled();
    expect(voidPtSale).not.toHaveBeenCalled();
    fireEvent.click(within(unresolved).getByRole("button", { name: "Close" }));

    vi.mocked(listPtCommissions).mockResolvedValue(empty);
    vi.mocked(listCashSessions).mockResolvedValue({
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
    } as never);
    fireEvent.click(screen.getByRole("button", { name: /100.00 MMK/ }));
    await screen.findByText("No commission matched this sale.");
    fireEvent.click(screen.getByRole("button", { name: "Void sale" }));
    const dialog = await screen.findByRole("dialog", { name: "Void sale" });
    await within(dialog).findByRole("option", { name: /FRONT/ });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm void" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Select an open cash session.",
    );
    expect(voidPtSale).not.toHaveBeenCalled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Void sale" }));
    const ready = await screen.findByRole("dialog", { name: "Void sale" });
    fireEvent.change(
      within(ready).getByRole("textbox", { name: "Void reason" }),
      { target: { value: "Undo" } },
    );
    await within(ready).findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      within(ready).getByLabelText("Cash session for the payment reversal"),
      { target: { value: CASH } },
    );
    const pendingVoid = deferred<never>();
    vi.mocked(voidPtSale).mockReturnValue(pendingVoid.promise as never);
    fireEvent.click(
      within(ready).getByRole("button", { name: "Confirm void" }),
    );
    expect(ready).toHaveAttribute("aria-busy", "true");
    expectStillOpen(ready);
    await act(async () => {
      pendingVoid.resolve({} as never);
      await pendingVoid.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(voidPtSale).toHaveBeenCalledWith(
      SALE,
      expect.objectContaining({
        reason: "Undo",
        business_date: "2026-10-19",
        cash_session_id: CASH,
        payout_reversals: [],
      }),
      expect.stringMatching(KEY),
    );
    expect(await screen.findByText("Sale voided.")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    expect(listReceipts).not.toHaveBeenCalled();
  });

  it("prints an issued PT receipt from the sale and from approval", async () => {
    const paymentId = "77777777-7777-4777-8777-777777777777";
    const receiptId = "66666666-6666-4666-8666-666666666666";
    const posted = sale("POSTED", {
      payment_id: paymentId,
      payment: { method: "CARD", status: "POSTED", business_date: "2026-10-19" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([posted]));
    vi.mocked(getPtSale).mockResolvedValue(posted);
    const hidden = renderSales();
    await openSaleRow();
    expect(
      screen.queryByRole("link", { name: "Print receipt" }),
    ).not.toBeInTheDocument();
    expect(listReceipts).not.toHaveBeenCalled();
    hidden.unmount();

    vi.mocked(listReceipts).mockResolvedValue({
      data: [{ id: receiptId, payment_id: paymentId, status: "ISSUED" }],
      meta: { has_more: false, next_after: null },
    } as never);
    const viewed = renderSales({ canViewReceipts: true });
    await openSaleRow();
    const postedLink = await screen.findByRole("link", {
      name: "Print receipt",
    });
    expect(postedLink).toHaveAttribute("href", `/receipts/${receiptId}/print`);
    expect(postedLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(postedLink).toHaveAttribute("target", "_blank");
    expect(listReceipts).toHaveBeenCalledWith({
      payment: paymentId,
      status: "ISSUED",
      limit: "5",
    });
    viewed.unmount();

    const pending = sale("PENDING_APPROVAL", {
      payment: { method: "CARD", business_date: "2026-10-19" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([pending]));
    vi.mocked(getPtSale).mockResolvedValue(pending);
    vi.mocked(listReceipts).mockClear();
    vi.mocked(listReceipts).mockResolvedValue(empty);
    vi.mocked(approvePtSale).mockResolvedValue({
      receipt: { id: receiptId, status: "ISSUED" },
    } as never);
    renderSales({ canApprove: true, canViewReceipts: true });
    await openSaleRow();
    fireEvent.click(screen.getByRole("button", { name: "Approve sale" }));
    fireEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Approve sale" }),
      ).getByRole("button", { name: "Confirm approval" }),
    );
    expect(
      await screen.findByRole("link", { name: "Print receipt" }),
    ).toHaveAttribute("href", `/receipts/${receiptId}/print`);
    expect(listReceipts).not.toHaveBeenCalled();
  });
});

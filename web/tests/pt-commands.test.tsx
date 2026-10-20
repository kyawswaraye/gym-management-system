import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BookingsManager } from "@/features/pt/bookings-manager";
import { ClassesManager } from "@/features/pt/classes-manager";
import { CommissionsManager } from "@/features/pt/commissions-manager";
import { MemberPackagesManager } from "@/features/pt/member-packages-manager";
import { PackagesManager } from "@/features/pt/packages-manager";
import { SalesManager } from "@/features/pt/sales-manager";
import { TrainersManager } from "@/features/pt/trainers-manager";
import {
  adjustPackageLedger,
  attendClassBooking,
  attendPtBooking,
  cancelClassBooking,
  cancelClassOccurrence,
  cancelPtBooking,
  cancelPtSale,
  createClassBooking,
  createPtBooking,
  createPtSale,
  getPtCommission,
  getPtSale,
  listAssignedMembers,
  listAssignments,
  listClassBookings,
  listClassOccurrences,
  listClassSchedules,
  listClassTypes,
  listMemberPackages,
  listPackageLedger,
  listPtBookings,
  listPtCommissions,
  listPtSales,
  listServicePackages,
  listTrainers,
  noShowClassBooking,
  noShowPtBooking,
  recordPtCommissionPayment,
  rejectPtSale,
  submitPtSale,
  updateClassSchedule,
  updateClassType,
  updatePtSale,
  voidPtSale,
  approvePtSale,
} from "@/features/pt/client";
import { listCashSessions } from "@/features/finance/client";
import { listMembers } from "@/features/operations/client";

vi.mock("@/features/pt/client", () => ({
  listTrainers: vi.fn(),
  listAssignments: vi.fn(),
  listAssignedMembers: vi.fn(),
  createAssignment: vi.fn(),
  endAssignment: vi.fn(),
  cancelAssignment: vi.fn(),
  listServicePackages: vi.fn(),
  createServicePackage: vi.fn(),
  listClassTypes: vi.fn(),
  listMemberPackages: vi.fn(),
  grantMemberPackage: vi.fn(),
  listPackageLedger: vi.fn(),
  adjustPackageLedger: vi.fn(),
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
  recordPtCommissionPayment: vi.fn(),
  listPtBookings: vi.fn(),
  createPtBooking: vi.fn(),
  attendPtBooking: vi.fn(),
  noShowPtBooking: vi.fn(),
  cancelPtBooking: vi.fn(),
  createClassType: vi.fn(),
  updateClassType: vi.fn(),
  listClassSchedules: vi.fn(),
  createClassSchedule: vi.fn(),
  updateClassSchedule: vi.fn(),
  listClassOccurrences: vi.fn(),
  cancelClassOccurrence: vi.fn(),
  listClassBookings: vi.fn(),
  createClassBooking: vi.fn(),
  attendClassBooking: vi.fn(),
  noShowClassBooking: vi.fn(),
  cancelClassBooking: vi.fn(),
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
  listStaff: vi.fn(),
  listCheckIns: vi.fn(),
  listMemberCheckIns: vi.fn(),
  createCheckIn: vi.fn(),
  checkOutCheckIn: vi.fn(),
  scanTrainerCheckIn: vi.fn(),
}));

vi.mock("@/features/finance/client", () => ({
  listCashSessions: vi.fn(),
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const MEMBER = "22222222-2222-4222-8222-222222222222";
const SALE = "33333333-3333-4333-8333-333333333333";
const CASH = "44444444-4444-4444-8444-444444444444";
const COMMISSION = "55555555-5555-4555-8555-555555555555";
const PAYOUT = "66666666-6666-4666-8666-666666666666";
const CLASS_TYPE = "77777777-7777-4777-8777-777777777777";
const SCHEDULE = "88888888-8888-4888-8888-888888888888";
const OCCURRENCE = "99999999-9999-4999-8999-999999999999";
const BOOKING = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const GRANT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const LEDGER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const KEY = /^[A-Za-z0-9._:-]{16,128}$/;
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  } as never;
}

function sale(status: string, extra: Record<string, unknown> = {}) {
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
  };
}

describe("phase 5 command coverage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    for (const fn of [
      listTrainers,
      listAssignments,
      listAssignedMembers,
      listServicePackages,
      listClassTypes,
      listMemberPackages,
      listPackageLedger,
      listPtSales,
      listPtCommissions,
      listPtBookings,
      listClassSchedules,
      listClassOccurrences,
      listClassBookings,
      listMembers,
      listCashSessions,
    ]) {
      vi.mocked(fn).mockResolvedValue(empty);
    }
  });

  it("pages assigned members and resets when the trainer changes", async () => {
    vi.mocked(listTrainers).mockResolvedValue(
      page([
        { id: TRAINER, staff_code: "TR-1", display_name: "Ada" },
        { id: MEMBER, staff_code: "TR-2", display_name: "Grace" },
      ]),
    );
    const assignedMember = {
      id: MEMBER,
      member_number: "M-1",
      first_name: "Grace",
      last_name: "Hopper",
      assignment_id: SALE,
      effective_from: "2026-10-01",
      effective_until: null,
    };
    vi.mocked(listAssignedMembers).mockImplementation(async (_id, query) =>
      query?.after ? page([]) : page([assignedMember], "assigned-2"),
    );
    render(
      <TrainersManager canClose={false} canCreate={false} today="2026-10-19" />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Ada (TR-1)" }));
    expect(await screen.findByText(/Grace Hopper/)).toBeInTheDocument();
    const next = screen
      .getAllByRole("button", { name: "Next page" })
      .find((button) => !button.hasAttribute("disabled"));
    fireEvent.click(next!);
    await waitFor(() =>
      expect(listAssignedMembers).toHaveBeenCalledWith(TRAINER, {
        limit: "25",
        after: "assigned-2",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Grace (TR-2)" }));
    await waitFor(() =>
      expect(listAssignedMembers).toHaveBeenLastCalledWith(MEMBER, {
        limit: "25",
      }),
    );
  });

  it("shows a class type from a later lookup page", async () => {
    vi.mocked(listClassTypes)
      .mockResolvedValueOnce(
        page(
          [{ id: CLASS_TYPE, code: "YOGA", name: "Yoga", is_active: true }],
          "type-2",
        ),
      )
      .mockResolvedValue(
        page([
          {
            id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            code: "SPIN",
            name: "Spin",
            is_active: true,
          },
        ]),
      );
    render(<PackagesManager canManage currency="MMK" />);
    fireEvent.click(screen.getByRole("button", { name: "Create package" }));
    expect(
      await screen.findByRole("option", { name: /SPIN/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/2,000/)).not.toBeInTheDocument();
  });

  it("pages a grant ledger and corrects an entry on the later page", async () => {
    const grant = {
      id: GRANT,
      member_id: MEMBER,
      service_package_id: CLASS_TYPE,
      package_code: "PT10",
      package_name: "Ten",
      status: "ACTIVE",
      remaining_sessions: 9,
      granted_sessions: 10,
      valid_from: "2026-10-01",
      valid_until: "2027-01-01",
    };
    vi.mocked(listMemberPackages).mockResolvedValue(page([grant]));
    vi.mocked(listPackageLedger).mockImplementation(async (_id, query) =>
      query?.after
        ? page([
            {
              id: BOOKING,
              reason: "ATTEND",
              session_delta: -1,
              occurred_at: "2026-10-02T00:00:00Z",
              pt_booking_id: SALE,
              class_booking_id: null,
            },
          ])
        : page(
            [
              {
                id: LEDGER,
                reason: "GRANT",
                session_delta: 10,
                occurred_at: "2026-10-01T00:00:00Z",
                pt_booking_id: null,
                class_booking_id: null,
              },
            ],
            "ledger-2",
          ),
    );
    vi.mocked(adjustPackageLedger).mockResolvedValue({} as never);
    render(<MemberPackagesManager canAdjust today="2026-10-19" />);
    fireEvent.click(
      await screen.findByRole("button", { name: /PT10 · Ten · member/ }),
    );
    expect(await screen.findByText(/GRANT/)).toBeInTheDocument();
    const next = screen
      .getAllByRole("button", { name: "Next page" })
      .find((button) => !button.hasAttribute("disabled"));
    fireEvent.click(next!);
    expect(await screen.findByText(/ATTEND/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Correct ledger" }));
    fireEvent.change(screen.getByLabelText("Ledger entry to reverse"), {
      target: { value: BOOKING },
    });
    fireEvent.change(screen.getByLabelText("Correction reason"), {
      target: { value: "Wrong attendance" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() =>
      expect(adjustPackageLedger).toHaveBeenCalledWith(
        GRANT,
        { reverses_entry_id: BOOKING, reason: "Wrong attendance" },
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.change(screen.getByLabelText("Grant status"), {
      target: { value: "ACTIVE" },
    });
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "ACTIVE", limit: "25" }),
      ),
    );
    expect(
      vi.mocked(listMemberPackages).mock.calls.at(-1)?.[0],
    ).not.toHaveProperty("after");
  });

  it("creates, edits, submits, approves, rejects, and cancels a PT sale", async () => {
    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
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
    vi.mocked(createPtSale).mockResolvedValue(sale("DRAFT") as never);
    render(
      <SalesManager
        canApprove={false}
        canCreate
        canSubmit={false}
        canUpdate={false}
        canVoid={false}
        currency="MMK"
        membershipId="creator"
        today="2026-10-19"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    await screen.findByRole("option", { name: /Ada/ });
    fireEvent.change(screen.getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search members" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Pack" },
    });
    fireEvent.change(screen.getByLabelText("Gross fee"), {
      target: { value: "100.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createPtSale).toHaveBeenCalled());
    const created = vi.mocked(createPtSale).mock.calls[0]?.[0];
    expect(created).toEqual(
      expect.objectContaining({
        member_id: MEMBER,
        trainer_staff_profile_id: TRAINER,
        description: "Pack",
        gross_fee: "100.00",
        service_package_id: null,
      }),
    );
    expect(created).not.toHaveProperty("payment");
    expect(vi.mocked(createPtSale).mock.calls[0]?.[1]).toMatch(KEY);
    cleanup();

    vi.mocked(listPtSales).mockResolvedValue(page([sale("DRAFT")]));
    vi.mocked(getPtSale).mockResolvedValue(sale("DRAFT") as never);
    vi.mocked(updatePtSale).mockResolvedValue(sale("DRAFT") as never);
    vi.mocked(submitPtSale).mockResolvedValue(
      sale("PENDING_APPROVAL") as never,
    );
    vi.mocked(cancelPtSale).mockResolvedValue(sale("CANCELLED") as never);
    render(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit
        canUpdate
        canVoid={false}
        currency="MMK"
        membershipId="creator"
        today="2026-10-19"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Pack|100.00 MMK/ }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    await screen.findByRole("option", { name: /Ada|Current trainer/ });
    fireEvent.change(screen.getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Updated" },
    });
    fireEvent.change(screen.getByLabelText("Payment draft"), {
      target: { value: "clear" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() =>
      expect(updatePtSale).toHaveBeenCalledWith(
        SALE,
        expect.objectContaining({ description: "Updated", payment: null }),
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit sale" }));
    await waitFor(() =>
      expect(submitPtSale).toHaveBeenCalledWith(
        SALE,
        expect.stringMatching(KEY),
      ),
    );
    await screen.findByText("Sale submitted.");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Submit sale" }),
      ).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel draft" }));
    fireEvent.change(screen.getByLabelText("Cancellation reason"), {
      target: { value: "Not needed" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm cancellation" }),
    );
    await waitFor(() =>
      expect(cancelPtSale).toHaveBeenCalledWith(
        SALE,
        { reason: "Not needed" },
        expect.stringMatching(KEY),
      ),
    );
    cleanup();

    vi.mocked(getPtSale).mockResolvedValue(
      sale("PENDING_APPROVAL", {
        payment: { method: "CASH", business_date: "2026-09-30" },
      }) as never,
    );
    vi.mocked(listPtSales).mockResolvedValue(
      page([
        sale("PENDING_APPROVAL", {
          payment: { method: "CASH", business_date: "2026-09-30" },
        }),
      ]),
    );
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
    vi.mocked(approvePtSale).mockResolvedValue({} as never);
    vi.mocked(rejectPtSale).mockResolvedValue(sale("REJECTED") as never);
    const view = render(
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
    await screen.findByRole("option", { name: /FRONT/ });
    expect(
      screen.getByText(/payment business date 2026-09-30/),
    ).toBeInTheDocument();
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({
        status: "OPEN",
        business_date: "2026-09-30",
        limit: "100",
      }),
    );
    expect(listCashSessions).not.toHaveBeenCalledWith(
      expect.objectContaining({ business_date: "2026-10-19" }),
    );
    fireEvent.change(screen.getByLabelText("Cash session for the payment"), {
      target: { value: CASH },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() =>
      expect(approvePtSale).toHaveBeenCalledWith(
        SALE,
        { cash_session_id: CASH },
        expect.stringMatching(KEY),
      ),
    );
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
    fireEvent.click(await screen.findByRole("button", { name: "Reject sale" }));
    fireEvent.change(screen.getByLabelText("Rejection reason"), {
      target: { value: "Wrong fee" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
    await waitFor(() =>
      expect(rejectPtSale).toHaveBeenCalledWith(
        SALE,
        { rejection_reason: "Wrong fee" },
        expect.stringMatching(KEY),
      ),
    );
  });

  it("voids a sale whose commission is on a later page and blocks a cursor cycle", async () => {
    const posted = sale("POSTED", { payment: { method: "CASH" } });
    const filler = (index: number) => ({
      id: `eeeeeeee-eeee-4eee-8eee-${String(index).padStart(12, "0")}`,
      pt_sale_id: `ffffffff-ffff-4fff-8fff-${String(index).padStart(12, "0")}`,
      trainer_staff_profile_id: TRAINER,
    });
    vi.mocked(listPtSales).mockResolvedValue(page([posted]));
    vi.mocked(getPtSale).mockResolvedValue(posted as never);
    vi.mocked(listPtCommissions)
      .mockResolvedValueOnce(
        page(
          Array.from({ length: 25 }, (_, index) => filler(index + 1)),
          "commission-2",
        ),
      )
      .mockResolvedValueOnce(
        page([
          {
            id: COMMISSION,
            pt_sale_id: SALE,
            trainer_staff_profile_id: TRAINER,
          },
        ]),
      );
    vi.mocked(getPtCommission).mockResolvedValue({
      id: COMMISSION,
      pt_sale_id: SALE,
      payments: [
        {
          id: PAYOUT,
          amount: "10.00",
          status: "POSTED",
          payment_method: "CASH",
        },
      ],
    } as never);
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
    vi.mocked(voidPtSale).mockResolvedValue({} as never);
    const view = render(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    fireEvent.change(screen.getByLabelText("Void reason"), {
      target: { value: "Undo" },
    });
    await screen.findAllByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByLabelText("Cash session for the payment reversal"),
      {
        target: { value: CASH },
      },
    );
    fireEvent.change(screen.getByLabelText("Payout destination for 10.00"), {
      target: { value: CASH },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    await waitFor(() =>
      expect(voidPtSale).toHaveBeenCalledWith(
        SALE,
        expect.objectContaining({
          reason: "Undo",
          cash_session_id: CASH,
          payout_reversals: [
            { commission_payment_id: PAYOUT, cash_session_id: CASH },
          ],
        }),
        expect.stringMatching(KEY),
      ),
    );
    expect(listPtCommissions).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ after: "commission-2", limit: "100" }),
    );
    cleanup();
    view.unmount();

    vi.mocked(listPtCommissions).mockReset();
    vi.mocked(listPtCommissions)
      .mockResolvedValueOnce(page([filler(1)], "cursor-again"))
      .mockResolvedValueOnce(page([filler], "cursor-again"));
    vi.mocked(voidPtSale).mockClear();
    render(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "cursor repeated",
    );
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    expect(screen.getByRole("button", { name: "Confirm void" })).toBeDisabled();
    expect(screen.getByText(/was not sent/)).toBeInTheDocument();
    expect(voidPtSale).not.toHaveBeenCalled();
  });

  it("records a full cash payout", async () => {
    const detail = {
      id: COMMISSION,
      pt_sale_id: SALE,
      trainer_staff_profile_id: TRAINER,
      outstanding_amount: "10.00",
      gym_share: "90.00",
      status: "ACCRUED",
      created_by_membership_id: "creator",
      payments: [],
    };
    vi.mocked(listPtCommissions).mockResolvedValue(page([detail]));
    vi.mocked(getPtCommission).mockResolvedValue(detail as never);
    vi.mocked(recordPtCommissionPayment).mockResolvedValue({
      commission_after: detail,
    } as never);
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
    render(
      <CommissionsManager
        canPay
        currency="MMK"
        membershipId="payer"
        today="2026-10-19"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Outstanding 10.00/ }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Record payout" }),
    );
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByLabelText("Cash session when the method is cash"),
      {
        target: { value: CASH },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save payout" }));
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledWith(
        COMMISSION,
        expect.objectContaining({
          amount: "10.00",
          payment_method: "CASH",
          cash_session_id: CASH,
        }),
        expect.stringMatching(KEY),
      ),
    );
  });

  it("selects a later-page session for the payment date and omits one for non-cash", async () => {
    const later = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const cashSale = sale("PENDING_APPROVAL", {
      payment: { method: "CASH", business_date: "2026-09-30" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([cashSale]));
    vi.mocked(getPtSale).mockResolvedValue(cashSale as never);
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      if (query?.after === "sess-2") {
        return {
          data: [
            {
              id: later,
              status: "OPEN",
              currency: "MMK",
              register_code: "BACK",
              business_date: "2026-09-30",
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
            business_date: "2026-09-30",
          },
        ],
        meta: { has_more: true, next_after: "sess-2" },
      } as never;
    });
    vi.mocked(approvePtSale).mockResolvedValue({} as never);
    const view = render(
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
    await screen.findByRole("option", { name: /BACK/ });
    expect(listCashSessions).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        after: "sess-2",
        business_date: "2026-09-30",
        limit: "100",
        status: "OPEN",
      }),
    );
    fireEvent.change(screen.getByLabelText("Cash session for the payment"), {
      target: { value: later },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() =>
      expect(approvePtSale).toHaveBeenCalledWith(
        SALE,
        { cash_session_id: later },
        expect.stringMatching(KEY),
      ),
    );
    cleanup();
    view.unmount();

    const cardSale = sale("PENDING_APPROVAL", {
      payment: { method: "CARD", business_date: "2026-09-30" },
    });
    vi.mocked(listPtSales).mockResolvedValue(page([cardSale]));
    vi.mocked(getPtSale).mockResolvedValue(cardSale as never);
    vi.mocked(listCashSessions).mockClear();
    vi.mocked(approvePtSale).mockClear();
    render(
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
    expect(screen.getByText(/no cash session is sent/)).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Cash session for the payment"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() =>
      expect(approvePtSale).toHaveBeenCalledWith(
        SALE,
        {},
        expect.stringMatching(KEY),
      ),
    );
    expect(listCashSessions).not.toHaveBeenCalled();
  });

  it("reloads void sessions when the business date changes and blocks a cursor cycle", async () => {
    const later = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const posted = sale("POSTED", { payment: { method: "CASH" } });
    vi.mocked(listPtSales).mockResolvedValue(page([posted]));
    vi.mocked(getPtSale).mockResolvedValue(posted as never);
    vi.mocked(listPtCommissions).mockResolvedValue(
      page([
        {
          id: COMMISSION,
          pt_sale_id: SALE,
          trainer_staff_profile_id: TRAINER,
        },
      ]),
    );
    vi.mocked(getPtCommission).mockResolvedValue({
      id: COMMISSION,
      pt_sale_id: SALE,
      payments: [
        {
          id: PAYOUT,
          amount: "10.00",
          status: "POSTED",
          payment_method: "CASH",
        },
      ],
    } as never);
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      if (query?.business_date === "2026-10-02") {
        if (query.after === "void-2") {
          return {
            data: [
              {
                id: later,
                status: "OPEN",
                currency: "MMK",
                register_code: "BACK",
                business_date: "2026-10-02",
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
              business_date: "2026-10-02",
            },
          ],
          meta: { has_more: true, next_after: "void-2" },
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
    vi.mocked(voidPtSale).mockResolvedValue({} as never);
    const view = render(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    await screen.findAllByRole("option", { name: /FRONT/ });
    fireEvent.change(screen.getByLabelText("Business date"), {
      target: { value: "2026-10-02" },
    });
    await screen.findAllByRole("option", { name: /BACK/ });
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({
        after: "void-2",
        business_date: "2026-10-02",
        status: "OPEN",
      }),
    );
    fireEvent.change(screen.getByLabelText("Void reason"), {
      target: { value: "Undo" },
    });
    fireEvent.change(
      screen.getByLabelText("Cash session for the payment reversal"),
      { target: { value: later } },
    );
    fireEvent.change(screen.getByLabelText("Payout destination for 10.00"), {
      target: { value: later },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    await waitFor(() =>
      expect(voidPtSale).toHaveBeenCalledWith(
        SALE,
        expect.objectContaining({
          business_date: "2026-10-02",
          cash_session_id: later,
          payout_reversals: [
            { commission_payment_id: PAYOUT, cash_session_id: later },
          ],
        }),
        expect.stringMatching(KEY),
      ),
    );
    cleanup();
    view.unmount();

    vi.mocked(listCashSessions).mockReset();
    vi.mocked(listCashSessions).mockImplementation(async (query) =>
      query?.after === "cursor-again"
        ? ({
            data: [],
            meta: { has_more: true, next_after: "cursor-again" },
          } as never)
        : ({
            data: [
              {
                id: CASH,
                status: "OPEN",
                currency: "MMK",
                register_code: "FRONT",
                business_date: "2026-10-19",
              },
            ],
            meta: { has_more: true, next_after: "cursor-again" },
          } as never),
    );
    vi.mocked(voidPtSale).mockClear();
    render(
      <SalesManager
        canApprove={false}
        canCreate={false}
        canSubmit={false}
        canUpdate={false}
        canVoid
        currency="MMK"
        membershipId="approver"
        today="2026-10-19"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /100.00 MMK/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Void sale" }));
    expect(
      (await screen.findAllByText(/cursor repeated/)).length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Confirm void" })).toBeDisabled();
    expect(voidPtSale).not.toHaveBeenCalled();
  });

  it("reloads payout sessions for the selected date and sends null when the method is not cash", async () => {
    const later = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const detail = {
      id: COMMISSION,
      pt_sale_id: SALE,
      trainer_staff_profile_id: TRAINER,
      outstanding_amount: "10.00",
      gym_share: "90.00",
      status: "ACCRUED",
      created_by_membership_id: "creator",
      payments: [],
    };
    vi.mocked(listPtCommissions).mockResolvedValue(page([detail]));
    vi.mocked(getPtCommission).mockResolvedValue(detail as never);
    vi.mocked(recordPtCommissionPayment).mockResolvedValue({
      commission_after: detail,
    } as never);
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      if (query?.business_date === "2026-10-03") {
        return {
          data: [
            {
              id: later,
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
    render(
      <CommissionsManager
        canPay
        currency="MMK"
        membershipId="payer"
        today="2026-10-19"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Outstanding 10.00/ }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Record payout" }),
    );
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(screen.getByLabelText("Business date"), {
      target: { value: "2026-10-03" },
    });
    await screen.findByRole("option", { name: /BACK/ });
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({
        business_date: "2026-10-03",
        status: "OPEN",
        limit: "100",
      }),
    );
    fireEvent.change(
      screen.getByLabelText("Cash session when the method is cash"),
      { target: { value: later } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save payout" }));
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledWith(
        COMMISSION,
        expect.objectContaining({
          business_date: "2026-10-03",
          payment_method: "CASH",
          cash_session_id: later,
        }),
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Record payout" }));
    fireEvent.change(screen.getByLabelText("Method"), {
      target: { value: "CARD" },
    });
    expect(screen.getByText(/no cash session is sent/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save payout" }));
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenLastCalledWith(
        COMMISSION,
        expect.objectContaining({
          payment_method: "CARD",
          cash_session_id: null,
        }),
        expect.stringMatching(KEY),
      ),
    );
  });

  it("books, attends, marks no-show, and cancels a PT session with the entered reason", async () => {
    const booking = {
      id: BOOKING,
      trainer_staff_profile_id: TRAINER,
      member_id: MEMBER,
      member_service_package_id: GRANT,
      starts_at: "2026-10-19T09:00:00Z",
      ends_at: "2026-10-19T10:00:00Z",
      unmetered: false,
      unmetered_reason: "",
      status: "BOOKED",
    };
    vi.mocked(listPtBookings).mockResolvedValue(page([booking]));
    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
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
    vi.mocked(listMemberPackages).mockResolvedValue(
      page([
        {
          id: GRANT,
          package_code: "PT10",
          package_name: "Ten",
          remaining_sessions: 4,
          status: "ACTIVE",
        },
      ]),
    );
    vi.mocked(createPtBooking).mockResolvedValue(booking as never);
    vi.mocked(attendPtBooking).mockResolvedValue(booking as never);
    vi.mocked(noShowPtBooking).mockResolvedValue(booking as never);
    vi.mocked(cancelPtBooking).mockResolvedValue(booking as never);
    render(
      <BookingsManager
        canManage
        canUnmetered={false}
        timeZone="UTC"
        today="2026-10-19"
      />,
    );
    expect(await screen.findByText(/Member 22222222/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mark attended" }));
    await waitFor(() =>
      expect(attendPtBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Mark no-show" }));
    await waitFor(() =>
      expect(noShowPtBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel booking" }));
    fireEvent.change(screen.getByLabelText("Cancellation reason"), {
      target: { value: "Member asked" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm booking cancellation" }),
    );
    await waitFor(() =>
      expect(cancelPtBooking).toHaveBeenCalledWith(
        BOOKING,
        { reason: "Member asked" },
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Book session" }));
    await screen.findByRole("option", { name: /Ada/ });
    fireEvent.change(screen.getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search members" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await screen.findByRole("option", { name: /PT10/ });
    fireEvent.change(screen.getByLabelText("Member package"), {
      target: { value: GRANT },
    });
    fireEvent.change(screen.getByLabelText("Starts"), {
      target: { value: "2026-10-20T09:00" },
    });
    fireEvent.change(screen.getByLabelText("Ends"), {
      target: { value: "2026-10-20T10:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save booking" }));
    await waitFor(() =>
      expect(createPtBooking).toHaveBeenCalledWith(
        expect.objectContaining({
          trainer_staff_profile_id: TRAINER,
          member_id: MEMBER,
          member_service_package_id: GRANT,
          unmetered: false,
        }),
        expect.stringMatching(KEY),
      ),
    );
  });

  it("patches a class type and schedule, then runs class booking commands", async () => {
    const classType = {
      id: CLASS_TYPE,
      code: "YOGA",
      name: "Yoga",
      description: "Morning",
      default_duration_minutes: 45,
      default_capacity: 12,
      is_active: true,
    };
    const schedule = {
      id: SCHEDULE,
      class_type_id: CLASS_TYPE,
      trainer_staff_profile_id: TRAINER,
      weekdays: [0, 2],
      local_start_time: "09:00:00",
      local_end_time: "10:00:00",
      effective_from: "2026-10-19",
      effective_until: "2026-12-31",
      location: "Studio",
      location_is_exclusive: true,
      capacity: 12,
      status: "ACTIVE",
    };
    const occurrence = {
      id: OCCURRENCE,
      class_schedule_id: SCHEDULE,
      class_type_id: CLASS_TYPE,
      trainer_staff_profile_id: TRAINER,
      location: "Studio",
      location_is_exclusive: true,
      capacity: 12,
      starts_at: "2026-10-19T09:00:00Z",
      ends_at: "2026-10-19T10:00:00Z",
      local_date: "2026-10-19",
      status: "SCHEDULED",
    };
    const booking = {
      id: BOOKING,
      class_occurrence_id: OCCURRENCE,
      member_id: MEMBER,
      member_service_package_id: GRANT,
      unmetered: false,
      unmetered_reason: "",
      status: "BOOKED",
      starts_at: "2026-10-19T09:00:00Z",
      ends_at: "2026-10-19T10:00:00Z",
    };
    vi.mocked(listClassTypes).mockResolvedValue(page([classType]));
    vi.mocked(listClassSchedules).mockResolvedValue(page([schedule]));
    vi.mocked(listClassOccurrences).mockResolvedValue(page([occurrence]));
    vi.mocked(listClassBookings).mockResolvedValue(page([booking]));
    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
    vi.mocked(updateClassType).mockResolvedValue(classType as never);
    vi.mocked(updateClassSchedule).mockResolvedValue(schedule as never);
    vi.mocked(cancelClassOccurrence).mockResolvedValue(occurrence as never);
    vi.mocked(attendClassBooking).mockResolvedValue(booking as never);
    vi.mocked(noShowClassBooking).mockResolvedValue(booking as never);
    vi.mocked(cancelClassBooking).mockResolvedValue(booking as never);
    vi.mocked(createClassBooking).mockResolvedValue(booking as never);
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
    vi.mocked(listMemberPackages).mockResolvedValue(
      page([
        {
          id: GRANT,
          package_code: "CLS",
          package_name: "Class",
          remaining_sessions: 3,
          status: "ACTIVE",
        },
      ]),
    );
    render(
      <ClassesManager
        canAttend
        canBook
        canLateCancel
        canManage
        canUnmetered={false}
        today="2026-10-19"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit class type" }),
    );
    const typeDialog = screen.getByRole("dialog", { name: "Edit class type" });
    fireEvent.change(within(typeDialog).getByLabelText("Name"), {
      target: { value: "Power yoga" },
    });
    fireEvent.change(within(typeDialog).getByLabelText("Status"), {
      target: { value: "false" },
    });
    fireEvent.click(
      within(typeDialog).getByRole("button", { name: "Save class type" }),
    );
    await waitFor(() => expect(updateClassType).toHaveBeenCalled());
    expect(vi.mocked(updateClassType).mock.calls[0]?.[1]).toEqual({
      name: "Power yoga",
      description: "Morning",
      default_duration_minutes: 45,
      default_capacity: 12,
      is_active: false,
    });
    expect(vi.mocked(updateClassType).mock.calls[0]?.[1]).not.toHaveProperty(
      "code",
    );

    fireEvent.click(screen.getByRole("button", { name: "Update schedule" }));
    const scheduleDialog = screen.getByRole("dialog", {
      name: "Update schedule",
    });
    expect(
      within(scheduleDialog).getByText(/still unbooked/),
    ).toBeInTheDocument();
    fireEvent.click(
      within(scheduleDialog).getByRole("button", { name: "Save schedule" }),
    );
    await waitFor(() => expect(updateClassSchedule).toHaveBeenCalled());
    expect(vi.mocked(updateClassSchedule).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        class_type_id: CLASS_TYPE,
        trainer_staff_profile_id: TRAINER,
        weekdays: [0, 2],
        local_start_time: "09:00",
        local_end_time: "10:00",
        effective_from: "2026-10-19",
        effective_until: "2026-12-31",
        location: "Studio",
        location_is_exclusive: true,
        capacity: 12,
      }),
    );
    expect(vi.mocked(updateClassSchedule).mock.calls[0]?.[2]).toMatch(KEY);

    fireEvent.click(screen.getByRole("button", { name: "Cancel occurrence" }));
    fireEvent.change(screen.getByLabelText("Occurrence cancellation reason"), {
      target: { value: "Instructor unavailable" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm occurrence cancellation" }),
    );
    await waitFor(() =>
      expect(cancelClassOccurrence).toHaveBeenCalledWith(
        OCCURRENCE,
        { reason: "Instructor unavailable" },
        expect.stringMatching(KEY),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Mark attended" }));
    await waitFor(() =>
      expect(attendClassBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Mark no-show" }));
    await waitFor(() =>
      expect(noShowClassBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel booking" }));
    fireEvent.change(
      screen.getByLabelText("Class booking cancellation reason"),
      {
        target: { value: "Inside cutoff" },
      },
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Confirm class booking cancellation",
      }),
    );
    await waitFor(() =>
      expect(cancelClassBooking).toHaveBeenCalledWith(
        BOOKING,
        { reason: "Inside cutoff" },
        expect.stringMatching(KEY),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Search members" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await screen.findByRole("option", { name: /CLS/ });
    fireEvent.change(screen.getByLabelText("Occurrence"), {
      target: { value: OCCURRENCE },
    });
    fireEvent.change(screen.getByLabelText("Class package"), {
      target: { value: GRANT },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save class booking" }));
    await waitFor(() =>
      expect(createClassBooking).toHaveBeenCalledWith(
        expect.objectContaining({
          class_occurrence_id: OCCURRENCE,
          member_id: MEMBER,
          member_service_package_id: GRANT,
        }),
        expect.stringMatching(KEY),
      ),
    );
    cleanup();

    vi.mocked(listClassTypes).mockResolvedValue(
      page([{ ...classType, is_active: false }]),
    );
    render(
      <ClassesManager
        canAttend={false}
        canBook={false}
        canLateCancel={false}
        canManage
        canUnmetered={false}
        today="2026-10-19"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit class type" }),
    );
    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "true" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save class type" }));
    await waitFor(() =>
      expect(updateClassType).toHaveBeenLastCalledWith(
        CLASS_TYPE,
        expect.objectContaining({ is_active: true }),
      ),
    );
    cleanup();

    render(
      <ClassesManager
        canAttend={false}
        canBook={false}
        canLateCancel={false}
        canManage={false}
        canUnmetered={false}
        today="2026-10-19"
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Edit class type" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create schedule" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel occurrence" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel booking" }),
    ).not.toBeInTheDocument();
  });
});

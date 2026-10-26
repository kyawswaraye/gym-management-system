import {
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
import { CheckInsManager } from "@/features/operations/check-ins-manager";
import {
  cancelAssignment,
  createAssignment,
  createClassBooking,
  createPtSale,
  createServicePackage,
  endAssignment,
  grantMemberPackage,
  listAssignedMembers,
  listAssignments,
  listClassBookings,
  listClassOccurrences,
  listClassSchedules,
  listClassTypes,
  listMemberPackages,
  listPtBookings,
  listPtCommissions,
  listPtSales,
  listServicePackages,
  listTrainers,
  recordPtCommissionPayment,
  voidPtSale,
  getPtCommission,
  getPtSale,
} from "@/features/pt/client";
import {
  listMembers,
  listStaff,
  scanTrainerCheckIn,
  listCheckIns,
} from "@/features/operations/client";
import { OperationError } from "@/features/operations/client";
import { listCashSessions } from "@/features/finance/client";

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
  listCashSessions: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const MEMBER = "22222222-2222-4222-8222-222222222222";
const ASSIGNMENT = "33333333-3333-4333-8333-333333333333";
const CASH = "44444444-4444-4444-8444-444444444444";
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  } as never;
}

describe("phase 5 workflows", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listTrainers).mockResolvedValue(empty);
    vi.mocked(listAssignments).mockResolvedValue(empty);
    vi.mocked(listAssignedMembers).mockResolvedValue(empty);
    vi.mocked(listServicePackages).mockResolvedValue(empty);
    vi.mocked(listClassTypes).mockResolvedValue(empty);
    vi.mocked(listMemberPackages).mockResolvedValue(empty);
    vi.mocked(listPtSales).mockResolvedValue(empty);
    vi.mocked(listPtCommissions).mockResolvedValue(empty);
    vi.mocked(listPtBookings).mockResolvedValue(empty);
    vi.mocked(listClassSchedules).mockResolvedValue(empty);
    vi.mocked(listClassOccurrences).mockResolvedValue(empty);
    vi.mocked(listClassBookings).mockResolvedValue(empty);
    vi.mocked(listMembers).mockResolvedValue(empty);
    vi.mocked(listStaff).mockResolvedValue(empty);
    vi.mocked(listCheckIns).mockResolvedValue(empty);
    vi.mocked(listCashSessions).mockResolvedValue(empty);
  });

  it("creates, ends, and cancels an assignment and pages the directory", async () => {
    vi.mocked(listTrainers)
      .mockResolvedValueOnce(
        page(
          [{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }],
          "cursor-2",
        ),
      )
      .mockResolvedValue(
        page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
      );
    vi.mocked(listAssignments).mockResolvedValue(
      page([
        {
          id: ASSIGNMENT,
          trainer_staff_profile_id: TRAINER,
          member_id: MEMBER,
          effective_from: "2026-10-01",
          effective_until: null,
          status: "ACTIVE",
          ended_at: null,
          reason: "",
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
          trainer: { id: TRAINER, staff_code: "TR-1", display_name: "Ada" },
          member: {
            id: MEMBER,
            member_number: "M-1",
            first_name: "Grace",
            last_name: "Hopper",
          },
        },
      ]),
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
    vi.mocked(createAssignment).mockResolvedValue({} as never);
    vi.mocked(endAssignment).mockResolvedValue({} as never);
    vi.mocked(cancelAssignment).mockResolvedValue({} as never);
    render(<TrainersManager canClose canCreate today="2026-10-19" />);
    expect(
      await screen.findByRole("button", { name: "Ada (TR-1)" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Next page" })[0]!);
    await waitFor(() =>
      expect(listTrainers).toHaveBeenCalledWith({
        limit: "25",
        after: "cursor-2",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create assignment" }));
    fireEvent.click(screen.getByRole("button", { name: "Search members" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    fireEvent.change(screen.getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save assignment" }));
    await waitFor(() =>
      expect(createAssignment).toHaveBeenCalledWith({
        trainer_staff_profile_id: TRAINER,
        member_id: MEMBER,
        effective_from: "2026-10-19",
        effective_until: null,
        reason: "",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "End assignment" }),
    );
    fireEvent.change(screen.getByLabelText("Reason for ending"), {
      target: { value: "Finished" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm end" }));
    await waitFor(() =>
      expect(endAssignment).toHaveBeenCalledWith(ASSIGNMENT, {
        effective_until: "2026-10-19",
        reason: "Finished",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel assignment" }),
    );
    fireEvent.change(screen.getByLabelText("Reason for cancelling"), {
      target: { value: "Mistake" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    await waitFor(() =>
      expect(cancelAssignment).toHaveBeenCalledWith(ASSIGNMENT, {
        reason: "Mistake",
      }),
    );
  });

  it("hides assignment commands without permission and creates a package", async () => {
    render(
      <TrainersManager canClose={false} canCreate={false} today="2026-10-19" />,
    );
    expect(
      screen.queryByRole("button", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    render(<PackagesManager canManage currency="MMK" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create package" }),
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ten sessions" },
    });
    fireEvent.change(screen.getByLabelText("Sessions"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("Validity"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByLabelText("Price"), {
      target: { value: "100.00" },
    });
    vi.mocked(createServicePackage).mockResolvedValue({} as never);
    fireEvent.click(screen.getByRole("button", { name: "Save package" }));
    await waitFor(() =>
      expect(createServicePackage).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "PT",
          name: "Ten sessions",
          session_count: 10,
          validity_value: 3,
          validity_unit: "MONTH",
          price: "100.00",
        }),
      ),
    );
  });

  it("grants a package only for an adjuster and surfaces a consumed void", async () => {
    vi.mocked(grantMemberPackage).mockResolvedValue({} as never);
    vi.mocked(listServicePackages).mockResolvedValue(
      page([{ id: TRAINER, code: "PT10", name: "Ten", type: "PT" }]),
    );
    const { rerender } = render(
      <MemberPackagesManager canAdjust={false} today="2026-10-19" />,
    );
    expect(
      screen.queryByRole("button", { name: "Grant package" }),
    ).not.toBeInTheDocument();
    rerender(<MemberPackagesManager canAdjust today="2026-10-19" />);
    fireEvent.click(screen.getByRole("button", { name: "Grant package" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Search members" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await screen.findByRole("option", { name: /PT · PT10/ });
    fireEvent.change(screen.getByLabelText("Package"), {
      target: { value: TRAINER },
    });
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "Courtesy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save grant" }));
    await waitFor(() =>
      expect(grantMemberPackage).toHaveBeenCalledWith(
        expect.objectContaining({
          member_id: MEMBER,
          complimentary: true,
          reason: "Courtesy",
        }),
        expect.stringMatching(/^[A-Za-z0-9._:-]{16,128}$/),
      ),
    );

    const sale = {
      id: ASSIGNMENT,
      member_id: MEMBER,
      trainer_staff_profile_id: TRAINER,
      service_date: "2026-10-19",
      description: "Pack",
      gross_fee: "100.00",
      currency: "MMK",
      commission_percent_snapshot: "10.00",
      commission_amount: "10.00",
      gym_share: "90.00",
      paid_amount: "100.00",
      outstanding_amount: "0.00",
      status: "POSTED",
      payment_id: null,
      payment: { method: "CASH" },
      service_package_id: null,
      package_price_snapshot: null,
      member_service_package_id: null,
      submitted_at: null,
      submitted_by_membership_id: "other",
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
    };
    vi.mocked(listPtSales).mockResolvedValue(page([sale as never]));
    vi.mocked(getPtSale).mockResolvedValue(sale as never);
    vi.mocked(listPtCommissions).mockResolvedValue(empty);
    vi.mocked(voidPtSale).mockRejectedValue(
      new OperationError("Consumed", {}, "PACKAGE_CONSUMED"),
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
    fireEvent.change(screen.getByLabelText("Void reason"), {
      target: { value: "Undo" },
    });
    await screen.findByRole("option", { name: /FRONT/ });
    fireEvent.change(
      screen.getByLabelText("Cash session for the payment reversal"),
      { target: { value: CASH } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm void" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "PACKAGE_CONSUMED",
    );
    expect(voidPtSale).toHaveBeenCalledWith(
      ASSIGNMENT,
      expect.objectContaining({
        reason: "Undo",
        business_date: "2026-10-19",
        cash_session_id: CASH,
        payout_reversals: [],
      }),
      expect.any(String),
    );
  });

  it("records a partial payout and books a metered session", async () => {
    const detail = {
      id: ASSIGNMENT,
      pt_sale_id: TRAINER,
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
    };
    vi.mocked(listPtCommissions).mockResolvedValue(page([detail]));
    vi.mocked(getPtCommission).mockResolvedValue(detail);
    vi.mocked(recordPtCommissionPayment).mockResolvedValue({
      commission_after: detail,
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
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "4.00" },
    });
    fireEvent.change(screen.getByLabelText("Method"), {
      target: { value: "BANK_TRANSFER" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save payout" }));
    await waitFor(() =>
      expect(recordPtCommissionPayment).toHaveBeenCalledWith(
        ASSIGNMENT,
        expect.objectContaining({
          amount: "4.00",
          payment_method: "BANK_TRANSFER",
          cash_session_id: null,
        }),
        expect.any(String),
      ),
    );

    vi.mocked(listPtBookings).mockResolvedValue(empty);
    vi.mocked(createPtSale).mockResolvedValue({} as never);
    render(
      <BookingsManager
        canManage={false}
        canUnmetered={false}
        timeZone="UTC"
        today="2026-10-19"
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Book session" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenCalledWith(
        expect.objectContaining({ view: "calendar", date_from: "2026-10-19" }),
      ),
    );
  });

  it("keeps class commands behind their permissions and scans only the trainer route", async () => {
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
      screen.queryByRole("button", { name: "Create class type" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create schedule" }),
    ).not.toBeInTheDocument();
    expect(createClassBooking).not.toHaveBeenCalled();

    vi.mocked(listTrainers).mockResolvedValue(
      page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
    );
    vi.mocked(listAssignedMembers).mockResolvedValue(
      page([
        {
          id: MEMBER,
          member_number: "M-1",
          first_name: "Grace",
          last_name: "Hopper",
          assignment_id: ASSIGNMENT,
          effective_from: "2026-10-01",
          effective_until: null,
        },
      ]),
    );
    vi.mocked(scanTrainerCheckIn).mockResolvedValue({
      id: ASSIGNMENT,
    } as never);
    render(
      <CheckInsManager
        canCreate={false}
        canScan
        canUpdate={false}
        membershipId="membership-1"
        timeZone="UTC"
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "Trainer QR scan" }),
    ).toBeInTheDocument();
    const opener = await screen.findByRole("button", {
      name: "Scan check-in code",
    });
    await waitFor(() => expect(opener).toBeEnabled());
    fireEvent.click(opener);
    fireEvent.change(await screen.findByLabelText("Assigned member"), {
      target: { value: MEMBER },
    });
    fireEvent.change(screen.getByLabelText("Check-in code"), {
      target: { value: "opaque-code" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Trainer QR scan" })).getByRole(
        "button",
        { name: "Scan check-in code" },
      ),
    );
    await waitFor(() =>
      expect(scanTrainerCheckIn).toHaveBeenCalledWith({
        memberId: MEMBER,
        payload: "opaque-code",
      }),
    );
    expect(screen.queryByText("opaque-code")).not.toBeInTheDocument();
  });
});

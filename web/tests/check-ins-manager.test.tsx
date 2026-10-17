import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  OperationError,
  checkOutCheckIn,
  createCheckIn,
  listCheckIns,
  listMemberCheckIns,
  listMembers,
} from "@/features/operations/client";
import { CheckInsManager } from "@/features/operations/check-ins-manager";
import type { CheckInRecord, Member } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {
    readonly fields: Record<string, string>;

    constructor(message: string, fields: Record<string, string> = {}) {
      super(message);
      this.fields = fields;
    }
  },
  checkOutCheckIn: vi.fn(),
  createCheckIn: vi.fn(),
  listCheckIns: vi.fn(),
  listMemberCheckIns: vi.fn(),
  listMembers: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const CHECK_IN_ID = "22222222-2222-4222-8222-222222222222";

function member(): Member {
  return {
    id: MEMBER_ID,
    member_number: "MEM-000001",
    first_name: "Ada",
    last_name: "Lovelace",
    normalized_full_name: "ada lovelace",
    phone: "",
    email: "",
    gender: null,
    date_of_birth: null,
    address: {
      address_line1: "",
      address_line2: "",
      city: "",
      region: "",
      postal_code: "",
      country_code: "",
    },
    join_date: "2026-01-01",
    emergency_contact_name: "",
    emergency_contact_phone: "",
    notes: "",
    profile_photo_asset_id: null,
    linked_user_id: null,
    is_pt_client: false,
    authz_version: 1,
    status: "ACTIVE",
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function visit(status: "OPEN" | "CHECKED_OUT"): CheckInRecord {
  return {
    id: CHECK_IN_ID,
    member: {
      id: MEMBER_ID,
      member_number: "MEM-000001",
      first_name: "Ada",
      last_name: "Lovelace",
    },
    membership_id: MEMBER_ID,
    check_in_at: "2026-10-17T03:30:00Z",
    check_out_at: status === "OPEN" ? null : "2026-10-17T04:30:00Z",
    method: "MANUAL",
    status,
    note: "Front desk",
    eligibility: {
      eligible: true,
      backdated: false,
      membership_id: MEMBER_ID,
      membership_status: "ACTIVE",
      plan_code: "MONTH",
      plan_name: "Monthly",
      start_date: "2026-10-01",
      expiry_date: "2026-10-31",
      evaluated_on: "2026-10-17",
    },
  };
}

describe("Check-ins manager", () => {
  beforeEach(() => {
    vi.mocked(listCheckIns).mockReset();
    vi.mocked(listMembers).mockReset();
    vi.mocked(listMemberCheckIns).mockReset();
    vi.mocked(createCheckIn).mockReset();
    vi.mocked(checkOutCheckIn).mockReset();
    vi.mocked(listCheckIns).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listMemberCheckIns).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
  });

  it("searches, checks in, shows a safe error, and checks out", async () => {
    vi.mocked(listMembers).mockResolvedValue({
      data: [member()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(createCheckIn)
      .mockRejectedValueOnce(new OperationError("No eligible membership."))
      .mockResolvedValueOnce(visit("OPEN"));
    vi.mocked(listMemberCheckIns)
      .mockResolvedValueOnce({
        data: [],
        meta: { has_more: false, next_after: null },
      })
      .mockResolvedValue({
        data: [visit("OPEN")],
        meta: { has_more: false, next_after: null },
      });
    vi.mocked(checkOutCheckIn).mockResolvedValue(visit("CHECKED_OUT"));

    render(<CheckInsManager canCreate canUpdate timeZone="Asia/Yangon" />);
    expect(
      await screen.findByText("Nobody is checked in."),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search active members"), {
      target: { value: "Ada" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Select Ada Lovelace (MEM-000001)",
      }),
    );
    expect(listMembers).toHaveBeenCalledWith({
      search: "Ada",
      status: "ACTIVE",
      limit: 25,
    });
    expect(await screen.findByText("No check-ins yet.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "Front desk" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No eligible membership.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Check in" }));
    expect(await screen.findByText("Checked in.")).toBeInTheDocument();
    expect(createCheckIn).toHaveBeenLastCalledWith(
      expect.objectContaining({
        memberId: MEMBER_ID,
        note: "Front desk",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Check out" }));
    await waitFor(() => {
      expect(checkOutCheckIn).toHaveBeenCalledWith(CHECK_IN_ID, undefined);
    });
    expect(await screen.findByText("Checked out.")).toBeInTheDocument();
  });
});

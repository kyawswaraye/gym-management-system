import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  OperationError,
  createCheckIn,
  listCheckIns,
  listMemberCheckIns,
} from "@/features/operations/client";

const memberId = "11111111-1111-4111-8111-111111111111";
const checkInId = "22222222-2222-4222-8222-222222222222";

function envelope(data: unknown) {
  return {
    success: true,
    data,
    meta: { has_more: false, next_after: null },
    message: "Success",
  };
}

describe("manual check-in client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("builds the open-visit and member-history queries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(envelope([])));

    await listCheckIns({ open: "true", limit: 25 });
    await listMemberCheckIns(memberId.toUpperCase());

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls[0]).toBe("/api/operations/check-ins?open=true&limit=25");
    expect(urls[1]).toBe(
      `/api/operations/members/${memberId}/check-ins/?limit=10`,
    );
  });

  it("sends a trimmed note and the idempotency key", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        if (String(input) === "/api/auth/csrf") {
          return Response.json({ data: { csrf_token: "csrf-token" } });
        }
        return Response.json(
          envelope({
            id: checkInId,
            member: {
              id: memberId,
              member_number: "MEM-000001",
              first_name: "Ada",
              last_name: "Lovelace",
            },
            membership_id: null,
            check_in_at: "2026-10-17T03:30:00Z",
            check_out_at: null,
            method: "MANUAL",
            status: "OPEN",
            note: "Front desk",
            eligibility: {
              eligible: true,
              backdated: false,
              membership_id: memberId,
              membership_status: "ACTIVE",
              plan_code: "MONTH",
              plan_name: "Monthly",
              start_date: "2026-10-01",
              expiry_date: "2026-10-31",
              evaluated_on: "2026-10-17",
            },
          }),
        );
      });

    await createCheckIn({
      memberId,
      note: "  Front desk  ",
      idempotencyKey: "desk-key-1234567890",
    });

    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(
      "desk-key-1234567890",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      member_id: memberId,
      note: "Front desk",
    });
  });

  it("rejects an invalid member id before calling the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(
      listCheckIns({ memberId: "not-a-uuid" }),
    ).rejects.toBeInstanceOf(OperationError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

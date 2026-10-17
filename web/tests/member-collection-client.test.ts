import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  listAllMembershipPlans,
  listMembers,
  listMembershipExpiry,
  listMembershipPlans,
  listMemberships,
  OperationError,
} from "@/features/operations/client";
import type {
  MemberListPage,
  MembershipExpiryQuery,
  MembershipPlanListPage,
} from "@/types/api";

const memberId = "11111111-1111-4111-8111-111111111111";
const planId = "22222222-2222-4222-8222-222222222222";
const membershipId = "33333333-3333-4333-8333-333333333333";

function envelope(
  data: unknown[],
  next: string | null = null,
  hasMore = next !== null,
) {
  return {
    success: true,
    data,
    meta: { has_more: hasMore, next_after: next },
    message: "Success",
  };
}

function pageUrl(input: string): URL {
  return new URL(input, "https://app.example.com");
}

describe("member and membership collection client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("builds the exact member, plan, membership, and expiry queries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(envelope([])));
    const cursor = "cursor.one:abc+def=";

    await listMembers({
      status: "ACTIVE",
      search: "  Ada  ",
      joined_from: "2026-01-01",
      joined_to: "2026-10-17",
      is_pt_client: true,
      limit: 25,
      after: cursor,
    });
    await listMembers({ is_pt_client: false, limit: 1 });
    await listMembershipPlans({ limit: 100, after: "plan.cursor" });
    await listMemberships({
      status: "SUSPENDED",
      member_id: memberId.toUpperCase(),
      payment_status: "PARTIALLY_REFUNDED",
      limit: 25,
      after: "membership.cursor",
    });
    await listMembershipExpiry({
      bucket: "within_7_days",
      limit: 25,
      after: "expiry.cursor",
    });

    const urls = fetchMock.mock.calls.map(([url]) => pageUrl(String(url)));
    expect(urls.map((url) => url.pathname)).toEqual([
      "/api/operations/members",
      "/api/operations/members",
      "/api/operations/membership-plans",
      "/api/operations/memberships",
      "/api/operations/memberships/expiry",
    ]);
    expect(urls[0]?.searchParams.get("status")).toBe("ACTIVE");
    expect(urls[0]?.searchParams.get("search")).toBe("Ada");
    expect(urls[0]?.searchParams.get("joined_from")).toBe("2026-01-01");
    expect(urls[0]?.searchParams.get("joined_to")).toBe("2026-10-17");
    expect(urls[0]?.searchParams.get("is_pt_client")).toBe("true");
    expect(urls[0]?.searchParams.get("limit")).toBe("25");
    expect(urls[0]?.searchParams.get("after")).toBe(cursor);
    expect(urls[0]?.search).toBe(
      `?status=ACTIVE&search=Ada&joined_from=2026-01-01&joined_to=2026-10-17&is_pt_client=true&limit=25&after=${encodeURIComponent(cursor)}`,
    );
    expect(urls[1]?.search).toBe("?is_pt_client=false&limit=1");
    expect(urls[2]?.search).toBe("?limit=100&after=plan.cursor");
    expect(urls[3]?.searchParams.get("member_id")).toBe(memberId);
    expect(urls[3]?.search).toBe(
      `?status=SUSPENDED&member_id=${memberId}&payment_status=PARTIALLY_REFUNDED&limit=25&after=membership.cursor`,
    );
    expect(urls[4]?.search).toBe(
      "?bucket=within_7_days&limit=25&after=expiry.cursor",
    );
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
      expect(init?.cache).toBe("no-store");
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
    }
  });

  it("omits unsupported filters and null PT flags", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(envelope([])));

    await listMembers({
      search: "Ada",
      is_pt_client: null,
      limit: 25,
      ...({ gym_id: "secret" } as object),
    });
    await listMembershipExpiry({
      bucket: "today",
      limit: 10,
      ...({ status: "ACTIVE", payment_status: "PAID" } as object),
    } as MembershipExpiryQuery);

    const memberUrl = pageUrl(String(fetchMock.mock.calls[0]?.[0]));
    const expiryUrl = pageUrl(String(fetchMock.mock.calls[1]?.[0]));
    expect(memberUrl.search).toBe("?search=Ada&limit=25");
    expect(memberUrl.searchParams.get("gym_id")).toBeNull();
    expect(memberUrl.searchParams.get("is_pt_client")).toBeNull();
    expect(expiryUrl.search).toBe("?bucket=today&limit=10");
    expect(expiryUrl.searchParams.get("status")).toBeNull();
    expect(expiryUrl.searchParams.get("payment_status")).toBeNull();
  });

  it("rejects invalid query values before calling the BFF", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const rejected = [
      () => listMembers({ limit: 0 }),
      () => listMembers({ limit: 101 }),
      () => listMembers({ limit: 1.5 }),
      () => listMembers({ search: "   " }),
      () => listMembers({ search: "a".repeat(101) }),
      () => listMembers({ status: "PENDING" as "ACTIVE" }),
      () => listMembers({ joined_from: "2026-02-31" }),
      () => listMembers({ joined_from: "2026-10-02", joined_to: "2026-10-01" }),
      () => listMembers({ after: "" }),
      () => listMembers({ after: "bad cursor" }),
      () => listMembershipPlans({ after: "x".repeat(2049) }),
      () => listMemberships({ member_id: "not-a-uuid" }),
      () => listMemberships({ payment_status: "VOID" as "PAID" }),
      () => listMembershipExpiry({ bucket: "soon" as "today" }),
    ];

    for (const request of rejected) {
      await expect(request()).rejects.toBeInstanceOf(OperationError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("parses collection envelopes and blank cursors", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: [{ id: memberId }],
          meta: { has_more: true, next_after: "" },
          message: "Success",
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: [{ id: planId }],
          message: "Success",
        }),
      )
      .mockResolvedValueOnce(Response.json({ success: true, data: null }));

    const members: MemberListPage = await listMembers();
    const plans: MembershipPlanListPage = await listMembershipPlans();
    expect(members.data).toEqual([{ id: memberId }]);
    expect(members.meta).toEqual({ has_more: true, next_after: null });
    expect(plans.meta).toEqual({ has_more: false, next_after: null });
    await expect(listMemberships()).rejects.toBeInstanceOf(OperationError);
  });

  it("walks plan pages, dedupes ids, and stops on a cursor cycle", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          envelope(
            [{ id: planId }, { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }],
            "cursor.next",
          ),
        ),
      )
      .mockResolvedValueOnce(
        Response.json(
          envelope([
            { id: planId },
            { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" },
          ]),
        ),
      );

    const plans = await listAllMembershipPlans();
    expect(plans.map((plan) => plan.id)).toEqual([
      planId,
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]);
    expect(pageUrl(String(fetchMock.mock.calls[0]?.[0])).search).toBe(
      "?limit=100",
    );
    expect(pageUrl(String(fetchMock.mock.calls[1]?.[0])).search).toBe(
      "?limit=100&after=cursor.next",
    );

    fetchMock.mockReset();
    fetchMock.mockImplementation(async () =>
      Response.json(envelope([{ id: planId }], "cursor.loop")),
    );
    await expect(listAllMembershipPlans()).rejects.toMatchObject({
      message: "The membership plan list could not be loaded.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops when the plan catalog exceeds 20 pages and does not return a partial list", async () => {
    let page = 0;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        page += 1;
        return Response.json(
          envelope(
            [
              {
                id: `00000000-0000-4000-8000-${String(page).padStart(12, "0")}`,
              },
            ],
            `cursor.${page}`,
          ),
        );
      });

    await expect(listAllMembershipPlans()).rejects.toMatchObject({
      message: "The membership plan list is too large to load.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(20);
    expect(page).toBe(20);
  });

  it("rejects a repeated plan cursor supplied by the server before another request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(envelope([{ id: membershipId }], "cursor.same")),
      )
      .mockResolvedValueOnce(
        Response.json(envelope([{ id: planId }], "cursor.same")),
      );

    await expect(listAllMembershipPlans()).rejects.toMatchObject({
      message: "The membership plan list could not be loaded.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

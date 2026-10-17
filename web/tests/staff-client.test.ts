import { beforeEach, describe, expect, it, vi } from "vitest";

import { getCsrfToken } from "@/features/auth/client";
import {
  activateStaff,
  getStaff,
  inviteStaff,
  listActiveRoleCatalog,
  listStaff,
  OperationError,
  suspendStaff,
  updateStaff,
} from "@/features/operations/client";

vi.mock("@/features/auth/client", () => ({
  getCsrfToken: vi.fn(async () => "csrf-token"),
  mutationHeaders: (token: string) => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  }),
}));

describe("staff browser client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses same-origin BFF routes and never sends a bearer token", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: [],
          meta: { has_more: false, next_after: null },
          message: "Success",
        }),
      );
    const staffId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

    await listStaff({ status: "ACTIVE", search: "Ada" });
    await getStaff(staffId);
    await listActiveRoleCatalog();
    await inviteStaff({
      email: "ada@example.test",
      first_name: "Ada",
      last_name: "Trainer",
      staff_code: "TR-1",
      display_name: "Ada Trainer",
      joined_date: "2026-10-17",
      role_ids: ["11111111-1111-4111-8111-111111111111"],
    });
    await updateStaff(staffId, { display_name: "Ada Coach" });
    await activateStaff(staffId, "Returned from leave");
    await suspendStaff(staffId, "Seasonal leave");

    expect(getCsrfToken).toHaveBeenCalledTimes(4);
    expect(
      fetchMock.mock.calls.map(([url, init]) => [url, init?.method]),
    ).toEqual([
      ["/api/operations/staff?status=ACTIVE&search=Ada", undefined],
      [`/api/operations/staff/${staffId}`, undefined],
      ["/api/operations/roles?limit=100", undefined],
      ["/api/operations/staff/invitations", "POST"],
      [`/api/operations/staff/${staffId}`, "PATCH"],
      [`/api/operations/staff/${staffId}/activate`, "POST"],
      [`/api/operations/staff/${staffId}/suspend`, "POST"],
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      expect(JSON.stringify(init ?? {})).not.toMatch(
        /access_token|refresh_token|invitation_token/,
      );
    }
  });

  it("keeps field errors available to the form", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      Response.json(
        {
          success: false,
          error: {
            code: "INVALID_REQUEST",
            message: "The request could not be processed.",
            fields: { email: ["Enter a valid email address."] },
          },
        },
        { status: 400 },
      ),
    );

    const failure = await listStaff().then(
      () => {
        throw new Error("Expected the staff list request to fail.");
      },
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(OperationError);
    expect(failure).toMatchObject({
      message: "Enter a valid email address.",
      fields: { email: "Enter a valid email address." },
    });
  });

  it("returns a page and forwards limit with an opaque cursor", async () => {
    const cursor = "opaque.cursor:value";
    const nextCursor = "opaque.cursor:next";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: [{ id: "staff-1" }],
          meta: { has_more: true, next_after: nextCursor },
          message: "Success",
        }),
      );

    const page = await listStaff({
      status: "ACTIVE",
      search: "Ada",
      limit: 25,
      after: cursor,
    });
    const url = new URL(
      String(fetchMock.mock.calls[0]?.[0]),
      "https://app.example.com",
    );

    expect(url.pathname).toBe("/api/operations/staff");
    expect(url.searchParams.get("status")).toBe("ACTIVE");
    expect(url.searchParams.get("search")).toBe("Ada");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("after")).toBe(cursor);
    expect([...url.searchParams.keys()]).toEqual([
      "status",
      "search",
      "limit",
      "after",
    ]);
    expect(page).toEqual({
      data: [{ id: "staff-1" }],
      meta: { has_more: true, next_after: nextCursor },
    });
  });
});

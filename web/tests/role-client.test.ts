import { beforeEach, describe, expect, it, vi } from "vitest";

import { getCsrfToken } from "@/features/auth/client";
import {
  archiveRole,
  createRole,
  getMembershipRoles,
  listAllPermissions,
  listActiveRoleCatalog,
  listAllRoles,
  listAssignableRoles,
  listPermissions,
  listRoles,
  OperationError,
  replaceMembershipRoles,
  replaceRolePermissions,
  updateRole,
} from "@/features/operations/client";

vi.mock("@/features/auth/client", () => ({
  getCsrfToken: vi.fn(async () => "csrf-token"),
  mutationHeaders: (token: string) => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  }),
}));

const roleId = "11111111-1111-4111-8111-111111111111";
const membershipId = "3a944485-5df4-449a-9877-e4b91d41ee52";

function page(data: unknown[], next: string | null = null) {
  return {
    success: true,
    data,
    meta: { has_more: next !== null, next_after: next },
    message: "Success",
  };
}

describe("role browser client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses same-origin role routes and never sends a bearer token", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(page([{ id: roleId }])));

    await listRoles({ limit: 25, after: "cursor.one" });
    await listPermissions({ limit: 10 });
    await getMembershipRoles(membershipId);
    await createRole({ key: "GUIDE", name: "Guide" });
    await updateRole(roleId, { name: "Guide lead" });
    await archiveRole(roleId);
    await replaceRolePermissions(roleId, ["members.view"]);
    await replaceMembershipRoles(membershipId, [roleId]);

    expect(getCsrfToken).toHaveBeenCalledTimes(5);
    expect(
      fetchMock.mock.calls.map(([url, init]) => [
        String(url),
        init?.method ?? "GET",
      ]),
    ).toEqual([
      ["/api/operations/roles?limit=25&after=cursor.one", "GET"],
      ["/api/operations/permissions?limit=10", "GET"],
      [`/api/operations/gym-memberships/${membershipId}/roles`, "GET"],
      ["/api/operations/roles", "POST"],
      [`/api/operations/roles/${roleId}`, "PATCH"],
      [`/api/operations/roles/${roleId}/archive`, "POST"],
      [`/api/operations/roles/${roleId}/permissions`, "PUT"],
      [`/api/operations/gym-memberships/${membershipId}/roles`, "PUT"],
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      expect(JSON.stringify(init ?? {})).not.toMatch(
        /access_token|refresh_token|Bearer/,
      );
    }
    expect(JSON.parse(String(fetchMock.mock.calls[5]?.[1]?.body))).toEqual({});
    expect(JSON.parse(String(fetchMock.mock.calls[6]?.[1]?.body))).toEqual({
      permission_codes: ["members.view"],
    });
    expect(JSON.parse(String(fetchMock.mock.calls[7]?.[1]?.body))).toEqual({
      role_ids: [roleId],
    });
  });

  it("walks role pages and stops on a repeated or exhausted cursor", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          page(
            [
              { id: roleId, key: "OWNER", is_active: true },
              {
                id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                key: "TRAINER",
                is_active: true,
              },
            ],
            "cursor.next",
          ),
        ),
      )
      .mockResolvedValueOnce(
        Response.json(
          page([
            {
              id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              key: "TRAINER",
              is_active: false,
            },
            {
              id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              key: "TRAINER",
              is_active: true,
            },
          ]),
        ),
      );

    const roles = await listActiveRoleCatalog();
    const first = new URL(
      String(fetchMock.mock.calls[0]?.[0]),
      "https://app.example.com",
    );
    const second = new URL(
      String(fetchMock.mock.calls[1]?.[0]),
      "https://app.example.com",
    );

    expect(first.searchParams.get("limit")).toBe("100");
    expect(first.searchParams.get("after")).toBeNull();
    expect(second.searchParams.get("after")).toBe("cursor.next");
    expect(roles.map((role) => role.key)).toEqual(["TRAINER"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      Response.json(
        page([
          {
            id: "55555555-5555-4555-8555-555555555555",
            key: "MANAGER",
            is_active: true,
            is_protected: true,
            permission_codes: ["members.view", "payments.approve"],
          },
          {
            id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            key: "RECEPTIONIST",
            is_active: true,
            is_protected: true,
            permission_codes: ["members.view"],
          },
          {
            id: "66666666-6666-4666-8666-666666666666",
            key: "APPROVER",
            is_active: true,
            is_protected: false,
            permission_codes: ["payments.approve"],
          },
        ]),
      ),
    );
    const offered = await listAssignableRoles({
      isOwner: false,
      livePermissions: ["members.view"],
    });
    expect(offered.map((role) => role.key)).toEqual(["RECEPTIONIST"]);

    fetchMock.mockImplementation(async () =>
      Response.json(page([{ id: roleId }], "cursor.loop")),
    );
    await expect(listAllRoles()).rejects.toBeInstanceOf(OperationError);
    expect(fetchMock.mock.calls.length).toBeLessThan(30);
  });

  it("rejects an invalid limit or resource id before calling the BFF", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(listPermissions({ limit: 101 })).rejects.toMatchObject({
      message: "Limit must be from 1 to 100.",
    });
    await expect(
      updateRole("not-a-uuid", { name: "Guide" }),
    ).rejects.toMatchObject({
      message: "Role was not found.",
    });
    await expect(getMembershipRoles("../roles")).rejects.toMatchObject({
      message: "Membership was not found.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops when the permission catalog exceeds the page cap", async () => {
    let pageNumber = 0;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        pageNumber += 1;
        const suffix = String(pageNumber).padStart(12, "0");
        return Response.json(
          page(
            [{ id: `00000000-0000-4000-8000-${suffix}` }],
            `cursor.${pageNumber}`,
          ),
        );
      });

    await expect(listAllPermissions()).rejects.toMatchObject({
      message: "The permission list is too large to load.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(20);
  });
});

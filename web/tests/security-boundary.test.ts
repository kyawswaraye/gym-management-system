import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authenticatedGet,
  authenticatedMutation,
  authMutation,
  stripCredentials,
} from "@/lib/api/server";
import { POST as acceptOwnerInvitation } from "@/app/api/auth/accept-owner-invitation/route";
import { POST as acceptStaffInvitation } from "@/app/api/auth/accept-staff-invitation/route";
import { POST as selectGym } from "@/app/api/auth/select-gym/route";
import {
  GET as gymGet,
  PATCH as gymPatch,
} from "@/app/api/operations/gym/route";
import * as rolesRoute from "@/app/api/operations/roles/route";
import {
  GET as roleDetailGet,
  PATCH as roleDetailPatch,
} from "@/app/api/operations/roles/[id]/route";
import { POST as roleArchive } from "@/app/api/operations/roles/[id]/archive/route";
import { PUT as rolePermissions } from "@/app/api/operations/roles/[id]/permissions/route";
import { GET as permissionsList } from "@/app/api/operations/permissions/route";
import {
  GET as membershipRolesGet,
  PUT as membershipRolesPut,
} from "@/app/api/operations/gym-memberships/[id]/roles/route";
import {
  GET as settingsGet,
  PATCH as settingsPatch,
} from "@/app/api/operations/settings/route";
import {
  GET as staffDetailGet,
  PATCH as staffDetailPatch,
} from "@/app/api/operations/staff/[id]/route";
import { POST as staffActivate } from "@/app/api/operations/staff/[id]/activate/route";
import { POST as staffSuspend } from "@/app/api/operations/staff/[id]/suspend/route";
import { POST as staffInvite } from "@/app/api/operations/staff/invitations/route";
import { GET as staffList } from "@/app/api/operations/staff/route";
import * as tenantAuditRoute from "@/app/api/operations/audit-logs/route";
import {
  DELETE as operationsDelete,
  GET as operationsGet,
  PATCH as operationsPatch,
  POST as operationsPost,
} from "@/app/api/operations/[...path]/route";
import { resetEnvForTests } from "@/lib/env";

describe("browser credential boundary", () => {
  beforeEach(() => {
    process.env.DJANGO_API_URL = "https://api.example.com";
    process.env.APP_ORIGINS = "https://app.example.com";
    process.env.ACCESS_EXPIRED_CODE = "ACCESS_TOKEN_EXPIRED";
    process.env.ALLOW_INSECURE_LOCALHOST_COOKIES = "false";
    process.env.BFF_SHARED_SECRET = "test-bff-shared-secret-32-chars-min";
    resetEnvForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetEnvForTests();
  });

  it("strips credentials at any response depth", () => {
    expect(
      stripCredentials({
        success: true,
        data: {
          access_token: "secret-access",
          refresh_token: "secret-refresh",
          gym_selection_ticket: "secret-ticket",
          user: { id: "1" },
          nested: [{ token: "secret", safe: true }],
        },
      }),
    ).toEqual({
      success: true,
      data: { user: { id: "1" }, nested: [{ safe: true }] },
    });
  });

  it("refreshes and retries exactly once only for the configured code", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "new-access",
            refresh_token: "new-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { user: { id: "1" } } }),
      );

    const csrf = "x".repeat(43);
    const request = new NextRequest("https://app.example.com/api/auth/me", {
      headers: {
        cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
        origin: "https://app.example.com",
        "x-csrf-token": csrf,
      },
    });
    const response = await authenticatedGet(request, "/api/v1/auth/me/");

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(await response.text()).not.toContain("new-access");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
  });

  it("does not refresh a generic authentication failure", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json(
        {
          success: false,
          error: { code: "INVALID_TOKEN", message: "Invalid" },
        },
        { status: 401 },
      ),
    );
    const request = new NextRequest("https://app.example.com/api/auth/me", {
      headers: { cookie: "gms_access=bad; gms_refresh=refresh" },
    });

    expect((await authenticatedGet(request, "/api/v1/auth/me/")).status).toBe(
      401,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes and retries an authenticated mutation exactly once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "new-mutation-access",
            refresh_token: "new-mutation-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { id: "member-1" } }),
      );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/operations/members",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ first_name: "Moe" }),
      },
    );

    const response = await authenticatedMutation(request, "/api/v1/members/");
    const [, retryInit] = fetchMock.mock.calls[2] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(new Headers(retryInit.headers).get("authorization")).toBe(
      "Bearer new-mutation-access",
    );
    expect(JSON.parse(String(retryInit.body))).toEqual({ first_name: "Moe" });
    expect(response.headers.get("set-cookie")).toContain(
      "new-mutation-refresh",
    );
    expect(await response.text()).not.toContain("new-mutation-access");
  });

  it("preserves rotated cookies when the retry is unavailable", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "rotated-access",
            refresh_token: "rotated-refresh",
          },
        }),
      )
      .mockRejectedValueOnce(new Error("retry unavailable"));
    const csrf = "x".repeat(43);
    const request = new NextRequest("https://app.example.com/api/auth/me", {
      headers: {
        cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
        origin: "https://app.example.com",
        "x-csrf-token": csrf,
      },
    });

    const response = await authenticatedGet(request, "/api/v1/auth/me/");

    expect(response.status).toBe(503);
    expect(response.headers.get("set-cookie")).toContain("rotated-refresh");
    expect(await response.text()).not.toContain("rotated-access");
  });

  it("allows only approved tenant operation paths", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ success: true, data: [] }));
    const request = new NextRequest(
      "https://app.example.com/api/operations/members",
      { headers: { cookie: "gms_access=access" } },
    );

    expect(
      (await authenticatedGet(request, "/api/v1/members/?search=approved"))
        .status,
    ).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const rejected = await authenticatedGet(request, "/api/v1/admin/secrets/");
    expect(rejected.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("allowlists only the nested member payment history GET", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: [],
          meta: { has_more: false, next_after: null },
        }),
      );
    const memberId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const request = new NextRequest(
      "https://app.example.com/api/operations/members",
      { headers: { cookie: "gms_access=access" } },
    );
    const cursor = "opaque.cursor:1700000000:signature";

    expect(
      (await authenticatedGet(request, `/api/v1/members/${memberId}/payments/`))
        .status,
    ).toBe(200);
    expect(
      (
        await authenticatedGet(
          request,
          `/api/v1/members/${memberId}/payments/?limit=25&after=${cursor}`,
        )
      ).status,
    ).toBe(200);
    expect((await authenticatedGet(request, "/api/v1/payments/")).status).toBe(
      200,
    );
    expect((await authenticatedGet(request, "/api/v1/receipts/")).status).toBe(
      200,
    );

    const rejected = [
      `/api/v1/members/${memberId}/payments/?gym_id=${memberId}`,
      `/api/v1/members/${memberId}/payments/?status=POSTED`,
      `/api/v1/members/${memberId}/payments/?limit=101`,
      `/api/v1/members/${memberId}/payments/?limit=0`,
      `/api/v1/members/${memberId}/payments/?limit=25&limit=10`,
      `/api/v1/members/${memberId}/payments/?after=`,
      `/api/v1/members/${memberId}/payments/extra/`,
      `/api/v1/members/${memberId.toUpperCase()}/payments/`,
      "/api/v1/receipts/?status=POSTED",
      "/api/v1/retail-sales/return/",
    ];
    for (const path of rejected) {
      expect((await authenticatedGet(request, path)).status).toBe(503);
    }

    const csrf = "x".repeat(43);
    const mutation = new NextRequest(
      "https://app.example.com/api/operations/members",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: "{}",
      },
    );
    expect(
      (
        await authenticatedMutation(
          mutation,
          `/api/v1/members/${memberId}/payments/`,
        )
      ).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `https://api.example.com/api/v1/members/${memberId}/payments/`,
    );
  });

  it("allowlists manual check-in reads and writes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: { id: "11111111-1111-4111-8111-111111111111" },
          meta: { has_more: false, next_after: null },
        }),
      );
    const memberId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const checkInId = "11111111-1111-4111-8111-111111111111";
    const request = new NextRequest(
      "https://app.example.com/api/operations/check-ins",
      { headers: { cookie: "gms_access=access" } },
    );
    const cursor = "opaque.cursor:1700000000:signature";

    expect(
      (await authenticatedGet(request, "/api/v1/check-ins/?open=true&limit=25"))
        .status,
    ).toBe(200);
    expect(
      (
        await authenticatedGet(
          request,
          `/api/v1/check-ins/?member_id=${memberId}&after=${cursor}`,
        )
      ).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, `/api/v1/check-ins/${checkInId}/`))
        .status,
    ).toBe(200);
    expect(
      (
        await authenticatedGet(
          request,
          `/api/v1/members/${memberId}/check-ins/?limit=10`,
        )
      ).status,
    ).toBe(200);

    const csrf = "x".repeat(43);
    const key = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const mutation = () =>
      new NextRequest("https://app.example.com/api/operations/check-ins", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          "idempotency-key": key,
        },
        body: JSON.stringify({ member_id: memberId }),
      });
    expect(
      (await authenticatedMutation(mutation(), "/api/v1/check-ins/")).status,
    ).toBe(200);
    const createInit = fetchMock.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(createInit.headers).get("Idempotency-Key")).toBe(key);
    expect(
      (
        await authenticatedMutation(
          mutation(),
          `/api/v1/check-ins/${checkInId}/check-out/`,
        )
      ).status,
    ).toBe(200);

    const rejected = [
      "/api/v1/check-ins/?open=yes",
      `/api/v1/check-ins/?member_id=${memberId.toUpperCase()}`,
      `/api/v1/check-ins/?gym_id=${memberId}`,
      "/api/v1/check-ins/scan/",
      "/api/v1/check-in-challenges/",
      `/api/v1/check-ins/${checkInId}/check-out/?open=true`,
      `/api/v1/members/${memberId.toUpperCase()}/check-ins/`,
    ];
    for (const path of rejected) {
      expect((await authenticatedGet(request, path)).status).toBe(503);
    }
    expect(
      (await authenticatedMutation(mutation(), "/api/v1/check-ins/scan/"))
        .status,
    ).toBe(503);
    expect(
      (await authenticatedMutation(mutation(), "/api/v1/check-in-challenges/"))
        .status,
    ).toBe(200);
  });

  it("allows only approved platform paths and forwards idempotency on retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "new-platform-access",
            refresh_token: "new-platform-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { gym: { code: "YGN-01" } } }),
      );
    const csrf = "x".repeat(43);
    const key = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const request = new NextRequest(
      "https://app.example.com/api/operations/admin/gyms",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          "idempotency-key": key,
        },
        body: JSON.stringify({ gym: { code: "YGN-01" } }),
      },
    );

    const response = await authenticatedMutation(
      request,
      "/api/v1/admin/gyms/",
    );
    const [, retryInit] = fetchMock.mock.calls[2] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(new Headers(retryInit.headers).get("Idempotency-Key")).toBe(key);

    const rejected = await authenticatedGet(
      new NextRequest("https://app.example.com/api/operations/admin/secrets", {
        headers: { cookie: "gms_access=access" },
      }),
      "/api/v1/admin/secrets/",
    );
    expect(rejected.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("allowlists only exact gym lifecycle and audit route shapes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { id: "gym-id" } }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/admin/gyms",
      { headers: { cookie: "gms_access=access" } },
    );
    const gymId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

    expect(
      (await authenticatedGet(request, `/api/v1/admin/gyms/${gymId}/`)).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, `/api/v1/admin/gyms/${gymId}/activate/`))
        .status,
    ).toBe(200);
    expect(
      (
        await authenticatedGet(
          request,
          `/api/v1/admin/audit-logs/?target_gym_id=${gymId}`,
        )
      ).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, `/api/v1/admin/gyms/${gymId}/owners/`))
        .status,
    ).toBe(200);
    expect((await authenticatedGet(request, "/api/v1/owners/")).status).toBe(
      200,
    );

    expect(
      (
        await authenticatedGet(
          request,
          "/api/v1/admin/gyms/not-a-uuid/suspend/",
        )
      ).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("allowlists exact dashboard and subscription detail routes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: {
            name: "Growth",
            access_token: "secret-access",
            refresh_token: "secret-refresh",
            nested: { token: "nested-secret" },
          },
        }),
      );
    const planId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const csrf = "x".repeat(43);
    const reader = new NextRequest(
      "https://app.example.com/api/operations/admin/dashboard",
      { headers: { cookie: "gms_access=access" } },
    );

    const dashboard = await operationsGet(reader, {
      params: Promise.resolve({ path: ["admin", "dashboard"] }),
    });
    const dashboardBody = await dashboard.text();
    expect(dashboard.status).toBe(200);
    expect(dashboardBody).toContain("Growth");
    expect(dashboardBody).not.toContain("secret-access");
    expect(dashboardBody).not.toContain("secret-refresh");
    expect(dashboardBody).not.toContain("nested-secret");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://api.example.com/api/v1/admin/dashboard/",
    );

    const queried = await operationsGet(
      new NextRequest(
        "https://app.example.com/api/operations/admin/dashboard?access_token=secret-access",
        { headers: { cookie: "gms_access=access" } },
      ),
      { params: Promise.resolve({ path: ["admin", "dashboard"] }) },
    );
    expect(queried.status).toBe(503);

    const extra = await operationsGet(reader, {
      params: Promise.resolve({ path: ["admin", "dashboard", "extra"] }),
    });
    expect(extra.status).toBe(503);
    expect(
      (
        await authenticatedGet(
          reader,
          "/api/v1/admin/dashboard/../subscriptions/",
        )
      ).status,
    ).toBe(503);
    expect(
      (await authenticatedGet(reader, "/api/v1/admin/dashboard/%2e%2e/"))
        .status,
    ).toBe(503);

    function patchRequest(origin = true) {
      return new NextRequest(
        `https://app.example.com/api/operations/admin/subscriptions/${planId}`,
        {
          method: "PATCH",
          headers: {
            cookie: `gms_access=access; gms_csrf=${csrf}`,
            ...(origin ? { origin: "https://app.example.com" } : {}),
            "content-type": "application/json",
            "x-csrf-token": csrf,
          },
          body: JSON.stringify({
            name: "Growth",
            access_token: "browser-access",
            refresh_token: "browser-refresh",
          }),
        },
      );
    }

    expect(
      (
        await operationsPatch(patchRequest(false), {
          params: Promise.resolve({
            path: ["admin", "subscriptions", planId],
          }),
        })
      ).status,
    ).toBe(403);

    const updated = await operationsPatch(patchRequest(), {
      params: Promise.resolve({ path: ["admin", "subscriptions", planId] }),
    });
    const updatedBody = await updated.text();
    const patchCall = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(updated.status).toBe(200);
    expect(patchCall[0]).toBe(
      `https://api.example.com/api/v1/admin/subscriptions/${planId}/`,
    );
    expect(patchCall[1].method).toBe("PATCH");
    expect(JSON.parse(String(patchCall[1].body))).toEqual({ name: "Growth" });
    expect(new Headers(patchCall[1].headers).get("authorization")).toBe(
      "Bearer access",
    );
    expect(updatedBody).not.toContain("secret-access");
    expect(updatedBody).not.toContain("browser-access");
    expect(updatedBody).not.toContain("browser-refresh");

    expect(
      (
        await operationsPatch(patchRequest(), {
          params: Promise.resolve({
            path: ["admin", "subscriptions", planId.toUpperCase()],
          }),
        })
      ).status,
    ).toBe(503);
    expect(
      (
        await operationsGet(
          new NextRequest(
            `https://app.example.com/api/operations/admin/subscriptions/${planId}?status=RETIRED`,
            { headers: { cookie: "gms_access=access" } },
          ),
          {
            params: Promise.resolve({
              path: ["admin", "subscriptions", planId],
            }),
          },
        )
      ).status,
    ).toBe(503);
    expect(
      (
        await operationsPatch(patchRequest(), {
          params: Promise.resolve({
            path: ["admin", "subscriptions", "not-a-uuid"],
          }),
        })
      ).status,
    ).toBe(503);
    expect(
      (
        await operationsPatch(patchRequest(), {
          params: Promise.resolve({
            path: ["admin", "subscriptions", planId, "features"],
          }),
        })
      ).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const listed = await authenticatedGet(
      reader,
      "/api/v1/admin/subscriptions/?status=ALL",
    );
    expect(listed.status).toBe(200);
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(
      "https://api.example.com/api/v1/admin/subscriptions/?status=ALL",
    );
  });

  it("fail-closes platform subscription and audit collection queries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { id: "plan-id" } }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/admin/subscriptions",
      { headers: { cookie: "gms_access=access" } },
    );
    const gymId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const secret = "query-secret-token-value";
    const closed = {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    };

    async function expectClosed(path: string) {
      const response = await authenticatedGet(request, path);
      const body = await response.text();
      expect(response.status).toBe(503);
      expect(JSON.parse(body)).toEqual(closed);
      expect(body).not.toContain(secret);
      expect(body).not.toContain("not-a-uuid");
      expect(fetchMock).not.toHaveBeenCalled();
    }

    await expectClosed("/api/v1/admin/subscriptions/?status=");
    await expectClosed("/api/v1/admin/subscriptions/?status=%20");
    await expectClosed("/api/v1/admin/subscriptions/?status=active");
    await expectClosed(`/api/v1/admin/subscriptions/?status=${"A".repeat(64)}`);
    await expectClosed(
      "/api/v1/admin/subscriptions/?status=ACTIVE&status=RETIRED",
    );
    await expectClosed(
      `/api/v1/admin/subscriptions/?status=ALL&access_token=${secret}`,
    );
    await expectClosed("/api/v1/admin/subscriptions/?limit=25");
    await expectClosed("/api/v1/admin/dashboard/?status=ALL");
    await expectClosed(`/api/v1/admin/subscriptions/${gymId}/?status=ACTIVE`);

    await expectClosed("/api/v1/admin/audit-logs/?target_gym_id=");
    await expectClosed("/api/v1/admin/audit-logs/?target_gym_id=not-a-uuid");
    await expectClosed(
      "/api/v1/admin/audit-logs/?target_gym_id=F52030C1-18AF-4E18-A1EC-50997D892EF4",
    );
    await expectClosed(
      `/api/v1/admin/audit-logs/?target_gym_id=${gymId}&target_gym_id=${gymId}`,
    );
    await expectClosed("/api/v1/admin/audit-logs/?action=");
    await expectClosed("/api/v1/admin/audit-logs/?action=%20%20");
    await expectClosed(`/api/v1/admin/audit-logs/?action=${"A".repeat(101)}`);
    await expectClosed("/api/v1/admin/audit-logs/?outcome=");
    await expectClosed("/api/v1/admin/audit-logs/?outcome=MAYBE");
    await expectClosed("/api/v1/admin/audit-logs/?limit=");
    await expectClosed("/api/v1/admin/audit-logs/?limit=0");
    await expectClosed("/api/v1/admin/audit-logs/?limit=01");
    await expectClosed("/api/v1/admin/audit-logs/?limit=101");
    await expectClosed("/api/v1/admin/audit-logs/?after=");
    await expectClosed(`/api/v1/admin/audit-logs/?after=${"a".repeat(2049)}`);
    await expectClosed("/api/v1/admin/audit-logs/?after=cursor%20secret");
    await expectClosed(`/api/v1/admin/audit-logs/?token=${secret}`);
    await expectClosed(
      `/api/v1/admin/audit-logs/?target_gym_id=${gymId}&refresh_token=${secret}`,
    );

    const cursor = `${"a".repeat(2033)}.1:abcDEF_12-zz`;
    expect(cursor).toHaveLength(2048);
    const accepted = [
      "/api/v1/admin/subscriptions/",
      "/api/v1/admin/subscriptions/?status=ACTIVE",
      "/api/v1/admin/subscriptions/?status=RETIRED",
      "/api/v1/admin/subscriptions/?status=ALL",
      "/api/v1/admin/audit-logs/",
      `/api/v1/admin/audit-logs/?target_gym_id=${gymId}&action=${"A".repeat(100)}&outcome=SUCCESS&limit=1&after=${encodeURIComponent(cursor)}`,
      `/api/v1/admin/audit-logs/?outcome=DENIED&limit=100`,
      `/api/v1/admin/audit-logs/?outcome=ERROR`,
      "/api/v1/admin/gyms/?search=ada&status=ACTIVE&token=kept-for-gyms",
    ];
    for (const path of accepted) {
      expect((await authenticatedGet(request, path)).status).toBe(200);
    }
    expect(fetchMock).toHaveBeenCalledTimes(accepted.length);
    const urls = fetchMock.mock.calls.map(([url]) => new URL(String(url)));
    expect(urls[0]?.pathname).toBe("/api/v1/admin/subscriptions/");
    expect(urls[0]?.search).toBe("");
    expect(urls[1]?.searchParams.get("status")).toBe("ACTIVE");
    expect(urls[2]?.searchParams.get("status")).toBe("RETIRED");
    expect(urls[3]?.searchParams.get("status")).toBe("ALL");
    expect([...(urls[1]?.searchParams.keys() ?? [])]).toEqual(["status"]);
    expect(urls[4]?.pathname).toBe("/api/v1/admin/audit-logs/");
    expect(urls[4]?.search).toBe("");
    expect(urls[5]?.searchParams.get("target_gym_id")).toBe(gymId);
    expect(urls[5]?.searchParams.get("action")).toBe("A".repeat(100));
    expect(urls[5]?.searchParams.get("outcome")).toBe("SUCCESS");
    expect(urls[5]?.searchParams.get("limit")).toBe("1");
    expect(urls[5]?.searchParams.get("after")).toBe(cursor);
    expect([...(urls[5]?.searchParams.keys() ?? [])]).toEqual([
      "target_gym_id",
      "action",
      "outcome",
      "limit",
      "after",
    ]);
    expect(urls[6]?.searchParams.get("outcome")).toBe("DENIED");
    expect(urls[6]?.searchParams.get("limit")).toBe("100");
    expect(urls[7]?.searchParams.get("outcome")).toBe("ERROR");
    expect(urls[8]?.pathname).toBe("/api/v1/admin/gyms/");
    expect(urls[8]?.searchParams.get("search")).toBe("ada");
    expect(urls[8]?.searchParams.get("status")).toBe("ACTIVE");
    expect(urls[8]?.searchParams.get("token")).toBe("kept-for-gyms");
  });

  it("rejects malformed idempotency keys before the upstream call", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/operations/admin/gyms",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          "idempotency-key": "short",
        },
        body: JSON.stringify({ gym: { code: "YGN-01" } }),
      },
    );

    const response = await authenticatedMutation(
      request,
      "/api/v1/admin/gyms/",
    );

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards gym profile and settings without browser tokens", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: {
            name: "Yangon Fitness",
            access_token: "secret-access",
            refresh_token: "secret-refresh",
          },
        }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/gym",
      { headers: { cookie: "gms_access=access" } },
    );

    const profile = await gymGet(request);
    const settings = await settingsGet(
      new NextRequest("https://app.example.com/api/operations/settings", {
        headers: { cookie: "gms_access=access" },
      }),
    );
    const profileBody = await profile.text();

    expect(profile.status).toBe(200);
    expect(settings.status).toBe(200);
    expect(profileBody).not.toContain("secret-access");
    expect(profileBody).not.toContain("secret-refresh");
    expect(profileBody).toContain("Yangon Fitness");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.example.com/api/v1/gym/",
      "https://api.example.com/api/v1/settings/",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("authorization")).toBe(
        "Bearer access",
      );
    }

    expect(
      (await authenticatedGet(request, "/api/v1/gym/activate/")).status,
    ).toBe(503);
    expect(
      (await authenticatedGet(request, "/api/v1/settings/next-member-number/"))
        .status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("enforces CSRF origin and referer on gym and settings updates", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const csrf = "x".repeat(43);
    const missing = new NextRequest(
      "https://app.example.com/api/operations/settings",
      {
        method: "PATCH",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({ week_starts_on: 0 }),
      },
    );
    const evil = new NextRequest("https://app.example.com/api/operations/gym", {
      method: "PATCH",
      headers: {
        cookie: `gms_access=access; gms_csrf=${csrf}`,
        origin: "https://evil.example",
        referer: "https://app.example.com/settings",
        "content-type": "application/json",
        "x-csrf-token": csrf,
      },
      body: JSON.stringify({ name: "Updated Gym" }),
    });

    expect((await settingsPatch(missing)).status).toBe(403);
    expect((await gymPatch(evil)).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock.mockResolvedValueOnce(
      Response.json({ success: true, data: { week_starts_on: 0 } }),
    );
    const refererOnly = new NextRequest(
      "https://app.example.com/api/operations/settings",
      {
        method: "PATCH",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          referer: "https://app.example.com/settings",
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({
          week_starts_on: 0,
          access_token: "browser-access",
          refresh_token: "browser-refresh",
        }),
      },
    );
    const response = await settingsPatch(refererOnly);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(url).toBe("https://api.example.com/api/v1/settings/");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ week_starts_on: 0 });
    expect(await response.text()).not.toContain("browser-access");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes a gym profile update once and strips rotated tokens", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "new-gym-access",
            refresh_token: "new-gym-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { name: "Updated Gym" } }),
      );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/operations/gym",
      {
        method: "PATCH",
        headers: {
          cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ name: "Updated Gym" }),
      },
    );

    const response = await gymPatch(request);
    const [, retryInit] = fetchMock.mock.calls[2] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "https://api.example.com/api/v1/gym/",
    );
    expect(new Headers(retryInit.headers).get("authorization")).toBe(
      "Bearer new-gym-access",
    );
    expect(JSON.parse(String(retryInit.body))).toEqual({ name: "Updated Gym" });
    expect(response.headers.get("set-cookie")).toContain("new-gym-refresh");
    expect(await response.text()).not.toContain("new-gym-access");
  });

  it("rejects encoded traversal before it can reach an auth route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const request = new NextRequest(
      "https://app.example.com/api/operations/members/%2e%2e/auth/me",
      { headers: { cookie: "gms_access=access" } },
    );

    const response = await authenticatedGet(
      request,
      "/api/v1/members/%2e%2e/auth/me/",
    );

    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stores the gym-selection ticket only in an HttpOnly cookie", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json({
        success: true,
        data: {
          requires_gym_selection: true,
          gym_selection_ticket: "secret-ticket",
          gym_selection_expires_at: new Date(
            Date.now() + 300_000,
          ).toISOString(),
          available_gym_memberships: [
            {
              gym_membership_id: "m1",
              gym_id: "g1",
              gym: { code: "A", name: "Alpha" },
            },
          ],
        },
      }),
    );
    const csrf = "x".repeat(43);
    const request = new NextRequest("https://app.example.com/api/auth/login", {
      method: "POST",
      headers: {
        cookie: `gms_csrf=${csrf}; gms_access=stale-access`,
        origin: "https://app.example.com",
        "x-csrf-token": csrf,
      },
    });
    const response = await authMutation(request, "/api/v1/auth/login/", {
      email: "owner@example.test",
      password: "correct horse battery staple",
    });
    const body = JSON.stringify(await response.json());
    const setCookie = response.headers.getSetCookie();
    const ticketCookie = setCookie.find((value) =>
      value.startsWith("gms_selection_ticket="),
    );
    const accessCookie = setCookie.find((value) =>
      value.startsWith("gms_access="),
    );
    const ticketMaxAge = Number(ticketCookie?.match(/Max-Age=(\d+)/)?.[1]);

    expect(response.status).toBe(200);
    expect(body).not.toContain("secret-ticket");
    expect(body).toContain("requires_gym_selection");
    expect(ticketCookie).toContain("secret-ticket");
    expect(ticketCookie).toContain("HttpOnly");
    expect(ticketMaxAge).toBeGreaterThan(0);
    expect(ticketMaxAge).toBeLessThanOrEqual(300);
    expect(accessCookie).toMatch(/Max-Age=0/);
  });

  it("stores the MFA challenge only in an HttpOnly cookie", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json({
        success: true,
        data: {
          requires_mfa: true,
          mfa_challenge_token: "secret-mfa-challenge",
          mfa_challenge_expires_at: new Date(
            Date.now() + 300_000,
          ).toISOString(),
        },
      }),
    );
    const csrf = "x".repeat(43);
    const request = new NextRequest("https://app.example.com/api/auth/login", {
      method: "POST",
      headers: {
        cookie: `gms_csrf=${csrf}`,
        origin: "https://app.example.com",
        "x-csrf-token": csrf,
      },
    });
    const response = await authMutation(request, "/api/v1/auth/login/", {
      email: "owner@example.test",
      password: "correct horse battery staple",
    });

    expect(JSON.stringify(await response.json())).not.toContain(
      "secret-mfa-challenge",
    );
    const challengeCookie = response.headers
      .getSetCookie()
      .find((value) => value.startsWith("gms_mfa_challenge="));
    expect(challengeCookie).toContain("secret-mfa-challenge");
    expect(challengeCookie).toContain("HttpOnly");
  });

  it("forwards the selection ticket without a leftover access token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json({
        success: true,
        data: {
          access_token: "new-access",
          refresh_token: "new-refresh",
        },
      }),
    );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/auth/select-gym",
      {
        method: "POST",
        headers: {
          cookie: `gms_csrf=${csrf}; gms_selection_ticket=cookie-ticket; gms_access=stale-access`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ gym_membership_id: "m1" }),
      },
    );
    const response = await selectGym(request);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const forwarded = JSON.parse(String(init.body)) as {
      gym_membership_id: string;
      gym_selection_ticket?: string;
    };
    const headers = new Headers(init.headers);

    expect(response.status).toBe(200);
    expect(forwarded).toEqual({
      gym_membership_id: "m1",
      gym_selection_ticket: "cookie-ticket",
    });
    expect(headers.get("authorization")).toBeNull();
    expect(headers.get("x-gym-bff-secret")).toBe(
      "test-bff-shared-secret-32-chars-min",
    );
    expect(JSON.stringify(await response.json())).not.toContain("new-access");
  });

  it("ignores a browser-supplied gym_selection_ticket", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json(
        {
          success: false,
          error: { code: "AUTHENTICATION_FAILED", message: "Invalid" },
        },
        { status: 401 },
      ),
    );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/auth/select-gym",
      {
        method: "POST",
        headers: {
          cookie: `gms_csrf=${csrf}; gms_access=stale-access`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          gym_membership_id: "m1",
          gym_selection_ticket: "browser-forged-ticket",
        }),
      },
    );
    await selectGym(request);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const forwarded = JSON.parse(String(init.body)) as Record<string, unknown>;

    expect(forwarded).toEqual({ gym_membership_id: "m1" });
    expect(forwarded.gym_selection_ticket).toBeUndefined();
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer stale-access",
    );
  });

  it("forwards staff reads and drops unknown list parameters", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: {
            display_name: "Ada",
            access_token: "secret-access",
            refresh_token: "secret-refresh",
          },
        }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/staff?status=ACTIVE&search=Ada&token=secret-token",
      { headers: { cookie: "gms_access=access" } },
    );
    const staffId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

    const list = await staffList(request);
    const detail = await staffDetailGet(
      new NextRequest(
        `https://app.example.com/api/operations/staff/${staffId}`,
        {
          headers: { cookie: "gms_access=access" },
        },
      ),
      { params: Promise.resolve({ id: staffId.toUpperCase() }) },
    );
    const roles = await rolesRoute.GET(
      new NextRequest("https://app.example.com/api/operations/roles", {
        headers: { cookie: "gms_access=access" },
      }),
    );
    const listBody = await list.text();

    expect(list.status).toBe(200);
    expect(detail.status).toBe(200);
    expect(roles.status).toBe(200);
    expect(listBody).toContain("Ada");
    expect(listBody).not.toContain("secret-access");
    expect(listBody).not.toContain("secret-refresh");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.example.com/api/v1/staff/?status=ACTIVE&search=Ada",
      `https://api.example.com/api/v1/staff/${staffId}/`,
      "https://api.example.com/api/v1/roles/",
    ]);
    expect(typeof rolesRoute.POST).toBe("function");
    expect("PATCH" in rolesRoute).toBe(false);
    expect("PUT" in rolesRoute).toBe(false);

    const rejected = await authenticatedGet(
      request,
      `/api/v1/staff/${staffId}/terminate/`,
    );
    expect(rejected.status).toBe(503);
    expect(
      (await authenticatedGet(request, `/api/v1/staff/${staffId}/archive/`))
        .status,
    ).toBe(503);
    expect(
      (await authenticatedGet(request, "/api/v1/roles/create/")).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("forwards a validated staff cursor and rejects unsafe list queries", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        success: true,
        data: [],
        meta: { has_more: false, next_after: null },
        message: "Success",
      }),
    );
    const cursor = ".eJy:1700000000:abcDEF_12-zz";
    const request = new NextRequest(
      `https://app.example.com/api/operations/staff?status=ACTIVE&membership_status=SUSPENDED&search=Ada&limit=25&after=${encodeURIComponent(cursor)}&token=secret-token&ordering=id`,
      { headers: { cookie: "gms_access=access" } },
    );

    const response = await staffList(request);
    const upstream = new URL(String(fetchMock.mock.calls[0]?.[0]));
    const responseText = await response.text();

    expect(response.status).toBe(200);
    expect(upstream.pathname).toBe("/api/v1/staff/");
    expect(upstream.searchParams.get("status")).toBe("ACTIVE");
    expect(upstream.searchParams.get("membership_status")).toBe("SUSPENDED");
    expect(upstream.searchParams.get("search")).toBe("Ada");
    expect(upstream.searchParams.get("limit")).toBe("25");
    expect(upstream.searchParams.get("after")).toBe(cursor);
    expect(upstream.searchParams.get("token")).toBeNull();
    expect(upstream.searchParams.get("ordering")).toBeNull();
    expect(responseText).not.toContain(cursor);
    expect(responseText).not.toContain("secret-token");

    fetchMock.mockClear();
    const rejectedCursor = "not a cursor";
    const rejected = [
      "limit=0",
      "limit=101",
      "limit=25abc",
      "limit=25&limit=50",
      `after=${encodeURIComponent(rejectedCursor)}`,
      `after=${"a".repeat(2049)}`,
    ];
    for (const query of rejected) {
      const denied = await staffList(
        new NextRequest(
          `https://app.example.com/api/operations/staff?${query}`,
          { headers: { cookie: "gms_access=access" } },
        ),
      );
      expect(denied.status).toBe(400);
      expect(await denied.text()).not.toContain(rejectedCursor);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an unknown staff id before calling Django", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const request = new NextRequest(
      "https://app.example.com/api/operations/staff/not-a-uuid",
      { headers: { cookie: "gms_access=access" } },
    );
    const context = { params: Promise.resolve({ id: "../auth/me" }) };

    expect((await staffDetailGet(request, context)).status).toBe(404);
    expect((await staffDetailPatch(request, context)).status).toBe(404);
    expect((await staffActivate(request, context)).status).toBe(404);
    expect((await staffSuspend(request, context)).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("enforces CSRF on staff invitation, profile, and suspension", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const csrf = "x".repeat(43);
    const staffId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const missingOrigin = new NextRequest(
      "https://app.example.com/api/operations/staff/invitations",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({ email: "ada@example.test" }),
      },
    );
    const evil = new NextRequest(
      `https://app.example.com/api/operations/staff/${staffId}`,
      {
        method: "PATCH",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://evil.example",
          referer: "https://app.example.com/staff",
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({ display_name: "Ada" }),
      },
    );

    expect((await staffInvite(missingOrigin)).status).toBe(403);
    expect(
      (
        await staffDetailPatch(evil, {
          params: Promise.resolve({ id: staffId }),
        })
      ).status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock.mockResolvedValueOnce(
      Response.json({ success: true, data: { status: "SUSPENDED" } }),
    );
    const refererOnly = new NextRequest(
      `https://app.example.com/api/operations/staff/${staffId}/suspend`,
      {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          referer: "https://app.example.com/staff",
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({
          reason: "Seasonal leave",
          access_token: "browser-access",
          refresh_token: "browser-refresh",
        }),
      },
    );
    const response = await staffSuspend(refererOnly, {
      params: Promise.resolve({ id: staffId }),
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(url).toBe(
      `https://api.example.com/api/v1/staff/${staffId}/suspend/`,
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ reason: "Seasonal leave" });
    expect(await response.text()).not.toContain("browser-access");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes a staff profile update once and strips rotated tokens", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "new-staff-access",
            refresh_token: "new-staff-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { display_name: "Ada" } }),
      );
    const csrf = "x".repeat(43);
    const staffId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const request = new NextRequest(
      `https://app.example.com/api/operations/staff/${staffId}`,
      {
        method: "PATCH",
        headers: {
          cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ display_name: "Ada" }),
      },
    );

    const response = await staffDetailPatch(request, {
      params: Promise.resolve({ id: staffId }),
    });
    const [, retryInit] = fetchMock.mock.calls[2] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `https://api.example.com/api/v1/staff/${staffId}/`,
    );
    expect(new Headers(retryInit.headers).get("authorization")).toBe(
      "Bearer new-staff-access",
    );
    expect(JSON.parse(String(retryInit.body))).toEqual({
      display_name: "Ada",
    });
    expect(response.headers.get("set-cookie")).toContain("new-staff-refresh");
    expect(await response.text()).not.toContain("new-staff-access");
  });

  it("stores staff invitation session cookies and keeps the token server-side", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json({
        success: true,
        data: {
          access_token: "staff-access",
          access_expires_at: new Date(Date.now() + 600_000).toISOString(),
          refresh_token: "staff-refresh",
          refresh_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
          requires_gym_selection: false,
          invitation_token: "echoed-token",
        },
      }),
    );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/auth/accept-staff-invitation",
      {
        method: "POST",
        headers: {
          cookie: `gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          invitation_token: "invite-token",
          password: "correct horse battery staple",
          password_confirmation: "correct horse battery staple",
          access_token: "browser-access",
          refresh_token: "browser-refresh",
        }),
      },
    );

    const blocked = await acceptStaffInvitation(
      new NextRequest(
        "https://app.example.com/api/auth/accept-staff-invitation",
        {
          method: "POST",
          headers: {
            cookie: `gms_csrf=${csrf}`,
            origin: "https://evil.example",
            "x-csrf-token": csrf,
            "content-type": "application/json",
          },
          body: JSON.stringify({ invitation_token: "invite-token" }),
        },
      ),
    );
    expect(blocked.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    const response = await acceptStaffInvitation(request);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = await response.text();
    const accessCookie = response.headers
      .getSetCookie()
      .find((value) => value.startsWith("gms_access="));
    const refreshCookie = response.headers
      .getSetCookie()
      .find((value) => value.startsWith("gms_refresh="));

    expect(response.status).toBe(200);
    expect(url).toBe(
      "https://api.example.com/api/v1/staff/invitations/accept/",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      invitation_token: "invite-token",
      password: "correct horse battery staple",
      password_confirmation: "correct horse battery staple",
    });
    expect(new Headers(init.headers).get("x-gym-bff-secret")).toBe(
      "test-bff-shared-secret-32-chars-min",
    );
    expect(new Headers(init.headers).get("authorization")).toBeNull();
    expect(body).not.toContain("staff-access");
    expect(body).not.toContain("staff-refresh");
    expect(body).not.toContain("invite-token");
    expect(body).not.toContain("echoed-token");
    expect(body).toContain("requires_gym_selection");
    expect(accessCookie).toContain("staff-access");
    expect(accessCookie).toContain("HttpOnly");
    expect(refreshCookie).toContain("staff-refresh");
    expect(refreshCookie).toContain("HttpOnly");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("forwards role and permission cursors and drops other query parameters", async () => {
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
    const cursor = ".eJy:1700000000:abcDEF_12-zz";
    const roles = await rolesRoute.GET(
      new NextRequest(
        `https://app.example.com/api/operations/roles?limit=25&after=${encodeURIComponent(cursor)}&token=secret-token&search=Ada`,
        { headers: { cookie: "gms_access=access" } },
      ),
    );
    const permissions = await permissionsList(
      new NextRequest(
        `https://app.example.com/api/operations/permissions?limit=100&after=${encodeURIComponent(cursor)}&code=platform.gyms.view`,
        { headers: { cookie: "gms_access=access" } },
      ),
    );
    const roleUrl = new URL(String(fetchMock.mock.calls[0]?.[0]));
    const permissionUrl = new URL(String(fetchMock.mock.calls[1]?.[0]));

    expect(roles.status).toBe(200);
    expect(permissions.status).toBe(200);
    expect(roleUrl.pathname).toBe("/api/v1/roles/");
    expect(roleUrl.searchParams.get("limit")).toBe("25");
    expect(roleUrl.searchParams.get("after")).toBe(cursor);
    expect(roleUrl.searchParams.get("token")).toBeNull();
    expect(roleUrl.searchParams.get("search")).toBeNull();
    expect(permissionUrl.pathname).toBe("/api/v1/permissions/");
    expect(permissionUrl.searchParams.get("code")).toBeNull();
    expect(await roles.text()).not.toContain("secret-token");

    fetchMock.mockClear();
    for (const query of [
      "limit=0",
      "limit=101",
      "limit=25&limit=50",
      "after=not a cursor",
    ]) {
      const denied = await rolesRoute.GET(
        new NextRequest(
          `https://app.example.com/api/operations/roles?${query}`,
          {
            headers: { cookie: "gms_access=access" },
          },
        ),
      );
      expect(denied.status).toBe(400);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects role and membership ids before calling Django", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const request = new NextRequest(
      "https://app.example.com/api/operations/roles/not-a-uuid",
      { headers: { cookie: "gms_access=access" } },
    );
    const context = { params: Promise.resolve({ id: "../auth/me" }) };

    expect((await roleDetailGet(request, context)).status).toBe(404);
    expect((await roleDetailPatch(request, context)).status).toBe(404);
    expect((await roleArchive(request, context)).status).toBe(404);
    expect((await rolePermissions(request, context)).status).toBe(404);
    expect((await membershipRolesGet(request, context)).status).toBe(404);
    expect((await membershipRolesPut(request, context)).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("enforces CSRF and forwards role permission replacement once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: { code: "ACCESS_TOKEN_EXPIRED", message: "Expired" },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "rotated-access",
            refresh_token: "rotated-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            id: "11111111-1111-4111-8111-111111111111",
            access_token: "should-not-leak",
            refresh_token: "should-not-leak-refresh",
          },
        }),
      );
    const csrf = "x".repeat(43);
    const roleId = "11111111-1111-4111-8111-111111111111";
    const membershipId = "3a944485-5df4-449a-9877-e4b91d41ee52";
    const context = { params: Promise.resolve({ id: roleId }) };
    const missingOrigin = new NextRequest(
      "https://app.example.com/api/operations/roles",
      {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({ key: "GUIDE", name: "Guide" }),
      },
    );
    const evil = new NextRequest(
      `https://app.example.com/api/operations/roles/${roleId}/permissions`,
      {
        method: "PUT",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://evil.example",
          referer: "https://app.example.com/roles",
          "content-type": "application/json",
          "x-csrf-token": csrf,
        },
        body: JSON.stringify({ permission_codes: ["members.view"] }),
      },
    );

    expect((await rolesRoute.POST(missingOrigin)).status).toBe(403);
    expect((await rolePermissions(evil, context)).status).toBe(403);
    expect(
      (
        await roleDetailPatch(
          new NextRequest(
            `https://app.example.com/api/operations/roles/${roleId}`,
            {
              method: "PATCH",
              headers: {
                cookie: `gms_access=access; gms_csrf=${csrf}`,
                origin: "https://evil.example",
                "content-type": "application/json",
                "x-csrf-token": csrf,
              },
              body: JSON.stringify({ name: "Guide" }),
            },
          ),
          context,
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await membershipRolesPut(
          new NextRequest(
            `https://app.example.com/api/operations/gym-memberships/${membershipId}/roles`,
            {
              method: "PUT",
              headers: {
                cookie: `gms_access=access; gms_csrf=${csrf}`,
                origin: "https://evil.example",
                "content-type": "application/json",
                "x-csrf-token": csrf,
              },
              body: JSON.stringify({ role_ids: [roleId] }),
            },
          ),
          { params: Promise.resolve({ id: membershipId }) },
        )
      ).status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    const response = await rolePermissions(
      new NextRequest(
        `https://app.example.com/api/operations/roles/${roleId}/permissions`,
        {
          method: "PUT",
          headers: {
            cookie: `gms_access=old; gms_refresh=refresh; gms_csrf=${csrf}`,
            origin: "https://app.example.com",
            "content-type": "application/json",
            "x-csrf-token": csrf,
          },
          body: JSON.stringify({
            permission_codes: ["members.view"],
            access_token: "browser-access",
            refresh_token: "browser-refresh",
          }),
        },
      ),
      context,
    );
    const body = await response.text();
    const calls = fetchMock.mock.calls.map(([url, init]) => [
      String(url),
      (init as RequestInit).method,
    ]);

    expect(response.status).toBe(200);
    expect(calls).toEqual([
      [`https://api.example.com/api/v1/roles/${roleId}/permissions/`, "PUT"],
      ["https://api.example.com/api/v1/auth/refresh/", "POST"],
      [`https://api.example.com/api/v1/roles/${roleId}/permissions/`, "PUT"],
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      permission_codes: ["members.view"],
    });
    expect(body).not.toContain("rotated-access");
    expect(body).not.toContain("should-not-leak");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      (
        await authenticatedGet(
          new NextRequest("https://app.example.com/api/operations/roles", {
            headers: { cookie: "gms_access=access" },
          }),
          `/api/v1/roles/${roleId}/delete/`,
        )
      ).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("strips provision request secrets and response credentials", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        success: true,
        data: {
          gym: { code: "YGN-02" },
          access_token: "secret-access",
          refresh_token: "secret-refresh",
          invitation_token: "plain-token",
          nested: { token: "nested-secret" },
        },
        message: "Gym provisioned",
      }),
    );
    const csrf = "x".repeat(43);
    const key = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const context = { params: Promise.resolve({ path: ["admin", "gyms"] }) };
    const blocked = await operationsPost(
      new NextRequest("https://app.example.com/api/operations/admin/gyms", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://evil.example",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          "idempotency-key": key,
        },
        body: JSON.stringify({
          owner: { send_invitation: true },
        }),
      }),
      context,
    );

    expect(blocked.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    const response = await operationsPost(
      new NextRequest("https://app.example.com/api/operations/admin/gyms", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          "idempotency-key": key,
        },
        body: JSON.stringify({
          gym: { code: "YGN-02" },
          owner: {
            email: "owner@example.test",
            send_invitation: true,
          },
          access_token: "browser-access",
          refresh_token: "browser-refresh",
          token: "browser-token",
        }),
      }),
      context,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(url).toBe("https://api.example.com/api/v1/admin/gyms/");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(key);
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer access",
    );
    expect(new Headers(init.headers).get("x-gym-bff-secret")).toBe(
      "test-bff-shared-secret-32-chars-min",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      gym: { code: "YGN-02" },
      owner: {
        email: "owner@example.test",
        send_invitation: true,
      },
    });
    expect(body).toContain("YGN-02");
    expect(body).not.toContain("secret-access");
    expect(body).not.toContain("secret-refresh");
    expect(body).not.toContain("plain-token");
    expect(body).not.toContain("nested-secret");
    expect(body).not.toContain("browser-access");
    expect(body).not.toContain("browser-refresh");
    expect(body).not.toContain("browser-token");
  });

  it("accepts an Owner invitation without a bearer credential", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      Response.json({
        success: true,
        data: {
          access_token: "owner-access",
          access_expires_at: new Date(Date.now() + 600_000).toISOString(),
          refresh_token: "owner-refresh",
          refresh_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
          requires_gym_selection: false,
          invitation_token: "echoed-token",
        },
      }),
    );
    const csrf = "x".repeat(43);
    const blocked = await acceptOwnerInvitation(
      new NextRequest(
        "https://app.example.com/api/auth/accept-owner-invitation",
        {
          method: "POST",
          headers: {
            cookie: `gms_csrf=${csrf}`,
            origin: "https://evil.example",
            "x-csrf-token": csrf,
            "content-type": "application/json",
          },
          body: JSON.stringify({ invitation_token: "invite-token" }),
        },
      ),
    );
    expect(blocked.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    const response = await acceptOwnerInvitation(
      new NextRequest(
        "https://app.example.com/api/auth/accept-owner-invitation",
        {
          method: "POST",
          headers: {
            cookie: `gms_csrf=${csrf}`,
            origin: "https://app.example.com",
            "x-csrf-token": csrf,
            "content-type": "application/json",
          },
          body: JSON.stringify({
            invitation_token: "invite-token",
            password: "correct horse battery staple",
            password_confirmation: "correct horse battery staple",
            access_token: "browser-access",
            refresh_token: "browser-refresh",
          }),
        },
      ),
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(url).toBe(
      "https://api.example.com/api/v1/owners/invitations/accept/",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      invitation_token: "invite-token",
      password: "correct horse battery staple",
      password_confirmation: "correct horse battery staple",
    });
    expect(new Headers(init.headers).get("authorization")).toBeNull();
    expect(new Headers(init.headers).get("x-gym-bff-secret")).toBe(
      "test-bff-shared-secret-32-chars-min",
    );
    expect(body).not.toContain("owner-access");
    expect(body).not.toContain("owner-refresh");
    expect(body).not.toContain("invite-token");
    expect(body).not.toContain("echoed-token");
    expect(body).not.toContain("browser-access");
    expect(response.headers.get("set-cookie")).toContain("owner-access");
  });

  it("fail-closes tenant audit queries and keeps them separate from platform audit", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: [
            {
              id: "34485f4c-d7df-4244-bae6-072868cc111a",
              action: "ROLE_CREATED",
              access_token: "secret-access",
              old_values: { token: "nested-secret", note: "kept" },
              new_values: { invitation_token: "invite-secret" },
            },
          ],
          meta: { has_more: false, next_after: null, scope: "SELF" },
          message: "Success",
          refresh_token: "secret-refresh",
        }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/audit-logs",
      { headers: { cookie: "gms_access=access" } },
    );
    const targetId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const secret = "query-secret-token-value";
    const email = "hidden-user@example.test";
    const closed = {
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    };

    async function expectClosed(path: string) {
      const response = await authenticatedGet(request, path);
      const body = await response.text();
      expect(response.status).toBe(503);
      expect(JSON.parse(body)).toEqual(closed);
      expect(body).not.toContain(secret);
      expect(body).not.toContain(email);
      expect(body).not.toContain("not-a-uuid");
      expect(body).not.toContain("SecretBrowser");
      expect(body).not.toContain("203.0.113.55");
      expect(body).not.toContain("hunter2-password");
      expect(fetchMock).not.toHaveBeenCalled();
    }

    await expectClosed("/api/v1/audit-logs/?action=");
    await expectClosed("/api/v1/audit-logs/?action=%20%20");
    await expectClosed(`/api/v1/audit-logs/?action=${"A".repeat(101)}`);
    await expectClosed("/api/v1/audit-logs/?module=");
    await expectClosed("/api/v1/audit-logs/?module=%20");
    await expectClosed(`/api/v1/audit-logs/?module=${"M".repeat(51)}`);
    await expectClosed("/api/v1/audit-logs/?target_type=");
    await expectClosed(`/api/v1/audit-logs/?target_type=${"T".repeat(101)}`);
    await expectClosed("/api/v1/audit-logs/?target_id=");
    await expectClosed("/api/v1/audit-logs/?target_id=not-a-uuid");
    await expectClosed(
      "/api/v1/audit-logs/?target_id=F52030C1-18AF-4E18-A1EC-50997D892EF4",
    );
    await expectClosed(
      `/api/v1/audit-logs/?target_id=${targetId}&target_id=${targetId}`,
    );
    await expectClosed("/api/v1/audit-logs/?outcome=");
    await expectClosed("/api/v1/audit-logs/?outcome=MAYBE");
    await expectClosed("/api/v1/audit-logs/?outcome=success");
    await expectClosed("/api/v1/audit-logs/?limit=");
    await expectClosed("/api/v1/audit-logs/?limit=0");
    await expectClosed("/api/v1/audit-logs/?limit=01");
    await expectClosed("/api/v1/audit-logs/?limit=101");
    await expectClosed("/api/v1/audit-logs/?after=");
    await expectClosed(`/api/v1/audit-logs/?after=${"a".repeat(2049)}`);
    await expectClosed("/api/v1/audit-logs/?after=cursor%20secret");
    await expectClosed(`/api/v1/audit-logs/?token=${secret}`);
    await expectClosed(`/api/v1/audit-logs/?password=hunter2-password`);
    await expectClosed(
      `/api/v1/audit-logs/?email=${encodeURIComponent(email)}`,
    );
    await expectClosed("/api/v1/audit-logs/?gym_id=gym-should-not-echo");
    await expectClosed("/api/v1/audit-logs/?ip_address=203.0.113.55");
    await expectClosed("/api/v1/audit-logs/?user_agent=SecretBrowser/9");
    await expectClosed(
      `/api/v1/audit-logs/?action=ROLE_CREATED&refresh_token=${secret}`,
    );
    await expectClosed(`/api/v1/audit-logs/?target_gym_id=${targetId}`);
    await expectClosed("/api/v1/audit-logs/?module=roles&module=staff");
    await expectClosed("/api/v1/audit-logs/extra/");
    await expectClosed(
      `/api/v1/admin/audit-logs/?module=roles&target_gym_id=${targetId}`,
    );

    const cursor = `${"a".repeat(2033)}.1:abcDEF_12-zz`;
    expect(cursor).toHaveLength(2048);
    const accepted = [
      "/api/v1/audit-logs/",
      `/api/v1/audit-logs/?action=${"A".repeat(100)}&module=${"m".repeat(50)}&outcome=DENIED&target_type=${"T".repeat(100)}&target_id=${targetId}&limit=1&after=${encodeURIComponent(cursor)}`,
      "/api/v1/audit-logs/?outcome=SUCCESS&limit=100",
      "/api/v1/audit-logs/?outcome=ERROR",
      `/api/v1/admin/audit-logs/?target_gym_id=${targetId}&outcome=SUCCESS&limit=25`,
    ];
    for (const path of accepted) {
      expect((await authenticatedGet(request, path)).status).toBe(200);
    }
    const urls = fetchMock.mock.calls.map(([url]) => new URL(String(url)));
    expect(urls[0]?.pathname).toBe("/api/v1/audit-logs/");
    expect(urls[0]?.search).toBe("");
    expect(urls[1]?.pathname).toBe("/api/v1/audit-logs/");
    expect(urls[1]?.searchParams.get("action")).toBe("A".repeat(100));
    expect(urls[1]?.searchParams.get("module")).toBe("m".repeat(50));
    expect(urls[1]?.searchParams.get("outcome")).toBe("DENIED");
    expect(urls[1]?.searchParams.get("target_type")).toBe("T".repeat(100));
    expect(urls[1]?.searchParams.get("target_id")).toBe(targetId);
    expect(urls[1]?.searchParams.get("limit")).toBe("1");
    expect(urls[1]?.searchParams.get("after")).toBe(cursor);
    expect([...(urls[1]?.searchParams.keys() ?? [])]).toEqual([
      "action",
      "module",
      "outcome",
      "target_type",
      "target_id",
      "limit",
      "after",
    ]);
    expect(urls[2]?.searchParams.get("outcome")).toBe("SUCCESS");
    expect(urls[2]?.searchParams.get("limit")).toBe("100");
    expect(urls[3]?.searchParams.get("outcome")).toBe("ERROR");
    expect(urls[4]?.pathname).toBe("/api/v1/admin/audit-logs/");
    expect(urls[4]?.searchParams.get("target_gym_id")).toBe(targetId);
    expect(urls[4]?.searchParams.get("module")).toBeNull();

    const routeResponse = await tenantAuditRoute.GET(
      new NextRequest(
        `https://app.example.com/api/operations/audit-logs?module=roles&limit=25&token=${secret}`,
        { headers: { cookie: "gms_access=access" } },
      ),
    );
    const routeBody = await routeResponse.text();
    expect(routeResponse.status).toBe(503);
    expect(JSON.parse(routeBody)).toEqual(closed);
    expect(routeBody).not.toContain(secret);
    expect(fetchMock).toHaveBeenCalledTimes(accepted.length);

    const listed = await tenantAuditRoute.GET(
      new NextRequest(
        `https://app.example.com/api/operations/audit-logs?module=roles&outcome=DENIED&limit=25`,
        { headers: { cookie: "gms_access=access" } },
      ),
    );
    const listedBody = await listed.text();
    expect(listed.status).toBe(200);
    expect(listedBody).toContain("ROLE_CREATED");
    expect(listedBody).toContain("kept");
    expect(listedBody).not.toContain("secret-access");
    expect(listedBody).not.toContain("secret-refresh");
    expect(listedBody).not.toContain("nested-secret");
    expect(listedBody).not.toContain("invite-secret");
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(
      "https://api.example.com/api/v1/audit-logs/?module=roles&outcome=DENIED&limit=25",
    );

    const routeExports: Record<string, unknown> = tenantAuditRoute;
    expect(typeof routeExports.GET).toBe("function");
    expect(routeExports.POST).toBeUndefined();
    expect(routeExports.PATCH).toBeUndefined();
    expect(routeExports.PUT).toBeUndefined();
    const csrf = "x".repeat(43);
    const mutated = await operationsPost(
      new NextRequest("https://app.example.com/api/operations/audit-logs", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ action: "ROLE_CREATED", token: secret }),
      }),
      { params: Promise.resolve({ path: ["audit-logs"] }) },
    );
    const mutatedBody = await mutated.text();
    expect(mutated.status).toBe(503);
    expect(JSON.parse(mutatedBody)).toEqual(closed);
    expect(mutatedBody).not.toContain(secret);
    expect(mutatedBody).not.toContain("ROLE_CREATED");
    expect(fetchMock).toHaveBeenCalledTimes(accepted.length + 1);
  });

  it("allowlists member and membership collection queries and rejects unsafe ones", async () => {
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
    const request = new NextRequest(
      "https://app.example.com/api/operations/members",
      { headers: { cookie: "gms_access=access" } },
    );
    const memberId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    const cursor = "a".repeat(2048);
    const secret = "injected-secret";

    async function expectForwarded(path: string, query: string) {
      const response = await authenticatedGet(
        request,
        query ? `${path}?${query}` : path,
      );
      expect(response.status).toBe(200);
      const upstream = new URL(String(fetchMock.mock.calls.at(-1)?.[0]));
      expect(upstream.pathname).toBe(path);
      expect(upstream.searchParams.toString()).toBe(
        new URLSearchParams(query).toString(),
      );
    }

    const memberStatuses = [
      undefined,
      "ACTIVE",
      "EXPIRED",
      "SUSPENDED",
      "CANCELLED",
      "ARCHIVED",
    ];
    const memberDates = [
      {},
      { joined_from: "2026-01-01" },
      { joined_to: "2026-12-31" },
      { joined_from: "2024-02-29", joined_to: "2024-02-29" },
      { joined_from: "2026-01-01", joined_to: "2026-10-17" },
    ];
    for (const status of memberStatuses) {
      for (const search of [undefined, "Ada Lovelace"]) {
        for (const dates of memberDates) {
          for (const pt of [undefined, "true", "false"]) {
            for (const limit of [undefined, "1", "100"]) {
              for (const after of [undefined, "cursor.one"]) {
                const params = new URLSearchParams();
                if (status) params.set("status", status);
                if (search) params.set("search", search);
                if (dates.joined_from)
                  params.set("joined_from", dates.joined_from);
                if (dates.joined_to) params.set("joined_to", dates.joined_to);
                if (pt) params.set("is_pt_client", pt);
                if (limit) params.set("limit", limit);
                if (after) params.set("after", after);
                await expectForwarded("/api/v1/members/", params.toString());
              }
            }
          }
        }
      }
    }

    const membershipStatuses = [
      undefined,
      "PENDING",
      "ACTIVE",
      "EXPIRED",
      "SUSPENDED",
      "CANCELLED",
    ];
    const paymentStatuses = [
      undefined,
      "UNPAID",
      "PARTIAL",
      "PAID",
      "PARTIALLY_REFUNDED",
      "REFUNDED",
    ];
    for (const status of membershipStatuses) {
      for (const payment of paymentStatuses) {
        for (const withMember of [false, true]) {
          for (const limit of [undefined, "25"]) {
            for (const after of [undefined, "cursor.one"]) {
              const params = new URLSearchParams();
              if (status) params.set("status", status);
              if (withMember) params.set("member_id", memberId);
              if (payment) params.set("payment_status", payment);
              if (limit) params.set("limit", limit);
              if (after) params.set("after", after);
              await expectForwarded("/api/v1/memberships/", params.toString());
            }
          }
        }
      }
    }

    for (const limit of [undefined, "1", "100"]) {
      for (const after of [undefined, cursor]) {
        const params = new URLSearchParams();
        if (limit) params.set("limit", limit);
        if (after) params.set("after", after);
        await expectForwarded("/api/v1/membership-plans/", params.toString());
      }
    }

    for (const bucket of [
      "expired",
      "today",
      "within_3_days",
      "within_7_days",
      "within_30_days",
    ]) {
      for (const limit of [undefined, "25"]) {
        for (const after of [undefined, "cursor.one"]) {
          const params = new URLSearchParams();
          params.set("bucket", bucket);
          if (limit) params.set("limit", limit);
          if (after) params.set("after", after);
          await expectForwarded(
            "/api/v1/memberships/expiry/",
            params.toString(),
          );
        }
      }
    }

    const rejected = [
      `/api/v1/members/?gym_id=${secret}`,
      "/api/v1/members/?status=ACTIVE&status=EXPIRED",
      "/api/v1/members/?search=",
      "/api/v1/members/?search=%20",
      `/api/v1/members/?search=${"a".repeat(101)}`,
      `/api/v1/members/?search=%00${secret}`,
      `/api/v1/members/?search=ada%26gym_id%3D${secret}`,
      "/api/v1/members/?status=active",
      "/api/v1/members/?status=PENDING",
      "/api/v1/members/?joined_from=2026-02-31",
      "/api/v1/members/?joined_from=2025-02-29",
      "/api/v1/members/?joined_from=2026-10-17&joined_to=2026-10-16",
      "/api/v1/members/?is_pt_client=1",
      "/api/v1/members/?is_pt_client=TRUE",
      "/api/v1/members/?limit=0",
      "/api/v1/members/?limit=101",
      "/api/v1/members/?limit=01",
      "/api/v1/members/?limit=25&limit=10",
      "/api/v1/members/?after=",
      `/api/v1/members/?after=${"a".repeat(2049)}`,
      "/api/v1/members/?after=bad%20cursor",
      "/api/v1/membership-plans/?ordering=code",
      `/api/v1/membership-plans/?after=%00${secret}`,
      "/api/v1/memberships/?status=ARCHIVED",
      `/api/v1/memberships/?member_id=${memberId.toUpperCase()}`,
      "/api/v1/memberships/?member_id=not-a-uuid",
      "/api/v1/memberships/?payment_status=VOID",
      "/api/v1/memberships/?member_id=&status=ACTIVE",
      `/api/v1/memberships/?gym_id=${secret}`,
      "/api/v1/memberships/expiry/",
      "/api/v1/memberships/expiry/?bucket=",
      "/api/v1/memberships/expiry/?bucket=soon",
      "/api/v1/memberships/expiry/?bucket=within_7_days&status=ACTIVE",
      `/api/v1/memberships/expiry/?bucket=today&payment_status=PAID&member_id=${memberId}`,
      `/api/v1/memberships/expiry/?bucket=expired%00&limit=25`,
      "/api/v1/memberships/expiry/?bucket=expired&bucket=today",
    ];
    const forwarded = fetchMock.mock.calls.length;
    for (const path of rejected) {
      const denied = await authenticatedGet(request, path);
      expect(denied.status).toBe(503);
      expect(await denied.text()).not.toContain(secret);
    }
    expect(fetchMock).toHaveBeenCalledTimes(forwarded);

    const csrf = "x".repeat(43);
    const mutationHeaders = {
      cookie: `gms_access=access; gms_csrf=${csrf}`,
      origin: "https://app.example.com",
      "x-csrf-token": csrf,
      "content-type": "application/json",
    };
    const queriedMutations = [
      "/api/v1/members/?search=ada",
      "/api/v1/membership-plans/?limit=1",
      "/api/v1/memberships/?status=ACTIVE",
      "/api/v1/memberships/expiry/?bucket=expired",
    ];
    for (const path of queriedMutations) {
      const denied = await authenticatedMutation(
        new NextRequest("https://app.example.com/api/operations/members", {
          method: "POST",
          headers: mutationHeaders,
          body: JSON.stringify({ first_name: "Moe" }),
        }),
        path,
      );
      expect(denied.status).toBe(503);
      expect(await denied.text()).not.toContain(secret);
    }
    expect(fetchMock).toHaveBeenCalledTimes(forwarded);

    for (const path of [
      "/api/v1/members/",
      "/api/v1/membership-plans/",
      "/api/v1/memberships/",
      `/api/v1/memberships/${memberId}/cancel/`,
    ]) {
      const allowed = await authenticatedMutation(
        new NextRequest("https://app.example.com/api/operations/members", {
          method: "POST",
          headers: mutationHeaders,
          body: "{}",
        }),
        path,
      );
      expect(allowed.status).toBe(200);
    }

    const routed = await operationsGet(
      new NextRequest(
        "https://app.example.com/api/operations/members?status=ACTIVE&limit=25",
        { headers: { cookie: "gms_access=access" } },
      ),
      { params: Promise.resolve({ path: ["members"] }) },
    );
    expect(routed.status).toBe(200);
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toBe(
      "https://api.example.com/api/v1/members/?status=ACTIVE&limit=25",
    );
    const blocked = await operationsGet(
      new NextRequest(
        `https://app.example.com/api/operations/memberships/expiry?bucket=today&gym_id=${secret}`,
        { headers: { cookie: "gms_access=access" } },
      ),
      { params: Promise.resolve({ path: ["memberships", "expiry"] }) },
    );
    expect(blocked.status).toBe(503);
    expect(await blocked.text()).not.toContain(secret);
    const detail = await authenticatedGet(
      request,
      `/api/v1/members/${memberId}/`,
    );
    expect(detail.status).toBe(200);
  });

  it("forwards only the canonical file routes and keeps upload signatures", async () => {
    const fileId = "11111111-1111-4111-8111-111111111111";
    const upload = {
      success: true,
      data: {
        file_id: fileId,
        upload: {
          url: "https://files.example.com/gym-private",
          fields: {
            key: "hidden-object",
            policy: "cG9saWN5",
            "x-amz-credential":
              "local-access/20261017/us-east-1/s3/aws4_request",
            "x-amz-signature": "ab".repeat(32),
          },
        },
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(upload));
    const csrf = "x".repeat(43);
    const headers = {
      cookie: `gms_access=access; gms_refresh=refresh; gms_csrf=${csrf}`,
      origin: "https://app.example.com",
      "x-csrf-token": csrf,
      "content-type": "application/json",
    };
    const intent = await authenticatedMutation(
      new NextRequest(
        "https://app.example.com/api/operations/files/upload-intents",
        {
          method: "POST",
          headers,
          body: JSON.stringify({ purpose: "PROFILE_PHOTO" }),
        },
      ),
      "/api/v1/files/upload-intents/",
    );
    expect(intent.status).toBe(200);
    expect(await intent.json()).toEqual(upload);
    const download = await authenticatedGet(
      new NextRequest("https://app.example.com/api/operations/files/download", {
        headers: { cookie: "gms_access=access" },
      }),
      `/api/v1/files/${fileId}/download-url/`,
    );
    expect(download.status).toBe(200);
    const removed = await operationsDelete(
      new NextRequest(
        `https://app.example.com/api/operations/files/${fileId}`,
        {
          method: "DELETE",
          headers,
        },
      ),
      { params: Promise.resolve({ path: ["files", fileId] }) },
    );
    expect(removed.status).toBe(200);
    const deleteCall = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(deleteCall[0]).toBe(
      `https://api.example.com/api/v1/files/${fileId}/`,
    );
    expect(deleteCall[1].method).toBe("DELETE");
    expect(deleteCall[1].body).toBeUndefined();
    const callsBeforeReject = fetchMock.mock.calls.length;
    const queried = await authenticatedGet(
      new NextRequest("https://app.example.com/api/operations/files/download", {
        headers: { cookie: "gms_access=access" },
      }),
      `/api/v1/files/${fileId}/download-url/?gym_id=1`,
    );
    const wrongMethod = await authenticatedMutation(
      new NextRequest("https://app.example.com/api/operations/files/download", {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      }),
      `/api/v1/files/${fileId}/download-url/`,
    );
    const bodyDelete = await authenticatedMutation(
      new NextRequest("https://app.example.com/api/operations/files/delete", {
        method: "DELETE",
        headers,
        body: JSON.stringify({ force: true }),
      }),
      `/api/v1/files/${fileId}/`,
      "DELETE",
    );
    expect(queried.status).toBe(503);
    expect(wrongMethod.status).toBe(503);
    expect(bodyDelete.status).toBe(400);
    expect(fetchMock.mock.calls.length).toBe(callsBeforeReject);
  });
});

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const file = path.join(directory, entry.name);
      return entry.isDirectory()
        ? sourceFiles(file)
        : /\.(ts|tsx)$/.test(entry.name)
          ? [file]
          : [];
    }),
  );
  return nested.flat();
}

it("contains no browser token storage usage", async () => {
  const files = await sourceFiles(path.resolve(process.cwd(), "src"));
  const forbidden = ["local" + "Storage", "session" + "Storage"];
  for (const file of files) {
    const source = await readFile(file, "utf8");
    for (const name of forbidden) expect(source).not.toContain(name);
  }
});

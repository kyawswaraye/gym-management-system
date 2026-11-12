import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authenticatedGet, authenticatedMutation } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";

describe("assistant BFF proxy", () => {
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

  it("forwards assistant reads and writes without an idempotency key", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: { available: true, reasons: [] },
        }),
      );
    const request = new NextRequest("https://app.example.com/api/operations", {
      headers: { cookie: "gms_access=access" },
    });

    expect(
      (await authenticatedGet(request, "/api/v1/assistant/status/")).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, "/api/v1/assistant/conversations/"))
        .status,
    ).toBe(200);
    expect(
      (
        await authenticatedGet(
          request,
          `/api/v1/assistant/conversations/${ID}/`,
        )
      ).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, "/api/v1/assistant/admin/")).status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const csrf = "x".repeat(43);
    const mutation = () =>
      new NextRequest("https://app.example.com/api/operations", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ message: "မင်္ဂလာပါ" }),
      });
    expect(
      (
        await authenticatedMutation(
          mutation(),
          "/api/v1/assistant/turns/",
          "POST",
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await authenticatedMutation(
          mutation(),
          `/api/v1/assistant/confirmations/${ID}/confirm/`,
          "POST",
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await authenticatedMutation(
          mutation(),
          `/api/v1/assistant/confirmations/${ID}/reject/`,
          "POST",
        )
      ).status,
    ).toBe(200);
    for (const call of fetchMock.mock.calls.slice(3)) {
      const headers = new Headers((call[1] as RequestInit).headers);
      expect(headers.get("Idempotency-Key")).toBeNull();
      expect(headers.get("Authorization")).toBe("Bearer access");
    }
  });

  it("refreshes a confirmation after the access cookie has expired", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "renewed-access",
            refresh_token: "renewed-refresh",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ success: true, data: { conversation_id: ID } }),
      );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/operations/assistant/confirm",
      {
        method: "POST",
        headers: {
          cookie: `gms_refresh=refresh; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ confirmed: true }),
      },
    );

    const response = await authenticatedMutation(
      request,
      `/api/v1/assistant/confirmations/${ID}/confirm/`,
    );
    const [, retry] = fetchMock.mock.calls[1] as [string, RequestInit];

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new Headers(retry.headers).get("authorization")).toBe(
      "Bearer renewed-access",
    );
    expect(response.headers.get("set-cookie")).toContain("renewed-refresh");
  });

  it("still asks for a sign-in when the refresh cookie is gone", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      "https://app.example.com/api/operations/assistant/confirm",
      {
        method: "POST",
        headers: {
          cookie: `gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
        },
        body: JSON.stringify({ confirmed: true }),
      },
    );

    const response = await authenticatedMutation(
      request,
      `/api/v1/assistant/confirmations/${ID}/confirm/`,
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(await response.json()).toMatchObject({
      error: { code: "AUTHENTICATION_REQUIRED" },
    });
  });
});

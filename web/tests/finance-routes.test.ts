import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { allowsFinanceRoute } from "@/lib/api/finance-routes";
import { authenticatedGet, authenticatedMutation } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";

describe("finance route allowlist", () => {
  it("accepts published list filters and the retail return command", () => {
    expect(
      allowsFinanceRoute(
        "/api/v1/cash-sessions/",
        "?status=OPEN&limit=25",
        "GET",
      ),
    ).toBe(true);
    expect(
      allowsFinanceRoute(
        "/api/v1/payments/",
        "?purpose=MEMBERSHIP&method=CASH",
        "GET",
      ),
    ).toBe(true);
    expect(
      allowsFinanceRoute(`/api/v1/retail-sales/${ID}/return/`, "", "POST"),
    ).toBe(true);
    expect(
      allowsFinanceRoute(`/api/v1/retail-sales/${ID}/return/`, "", "GET"),
    ).toBe(false);
    expect(
      allowsFinanceRoute(
        `/api/v1/retail-sales/${ID}/return/`,
        "?limit=1",
        "POST",
      ),
    ).toBe(false);
    expect(
      allowsFinanceRoute("/api/v1/payments/", "?gym_id=other", "GET"),
    ).toBe(false);
    expect(allowsFinanceRoute("/api/v1/cash-sessions/", "", "DELETE")).toBe(
      false,
    );
    expect(
      allowsFinanceRoute("/api/v1/receipts/print-context/", "", "GET"),
    ).toBe(true);
    expect(allowsFinanceRoute(`/api/v1/receipts/${ID}/print/`, "", "GET")).toBe(
      true,
    );
    expect(
      allowsFinanceRoute("/api/v1/receipts/print-context/", "?logo=1", "GET"),
    ).toBe(false);
    expect(
      allowsFinanceRoute(`/api/v1/receipts/${ID}/print/`, "", "POST"),
    ).toBe(false);
    expect(
      allowsFinanceRoute(
        `/api/v1/receipts/${ID.toUpperCase()}/print/`,
        "",
        "GET",
      ),
    ).toBeNull();
    expect(
      allowsFinanceRoute(
        "/api/v1/products/",
        "?search=WATER&is_active=true&limit=10",
        "GET",
      ),
    ).toBe(true);
    expect(
      allowsFinanceRoute("/api/v1/products/", `?search=${"x".repeat(101)}`, "GET"),
    ).toBe(false);
  });
});

describe("finance BFF proxy", () => {
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

  it("forwards a cash-session list and attaches a fresh idempotency key", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: [] }),
      );
    const request = new NextRequest("https://app.example.com/api/operations", {
      headers: { cookie: "gms_access=access" },
    });

    expect(
      (
        await authenticatedGet(
          request,
          "/api/v1/cash-sessions/?status=OPEN&limit=25",
        )
      ).status,
    ).toBe(200);
    expect(
      (await authenticatedGet(request, `/api/v1/retail-sales/${ID}/return/`))
        .status,
    ).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);

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
        body: JSON.stringify({ reason: "Counted short" }),
      });
    expect(
      (
        await authenticatedMutation(
          mutation(),
          `/api/v1/payments/${ID}/void/`,
          "POST",
        )
      ).status,
    ).toBe(200);
    const headers = new Headers(
      (fetchMock.mock.calls.at(-1)?.[1] as RequestInit).headers,
    );
    expect(headers.get("Idempotency-Key")).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
    expect(headers.get("Authorization")).toBe("Bearer access");

    expect(
      (
        await authenticatedMutation(
          mutation(),
          `/api/v1/retail-sales/${ID}/return/`,
          "POST",
        )
      ).status,
    ).toBe(200);
    const returnHeaders = new Headers(
      (fetchMock.mock.calls.at(-1)?.[1] as RequestInit).headers,
    );
    expect(returnHeaders.get("Idempotency-Key")).toMatch(
      /^[A-Za-z0-9._:-]{16,128}$/,
    );
    expect(returnHeaders.get("Authorization")).toBe("Bearer access");
  });

  it("does not invent an idempotency key for a check-in challenge", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: {} }),
      );
    const csrf = "x".repeat(43);
    const mutation = (idempotencyKey?: string) =>
      new NextRequest("https://app.example.com/api/operations", {
        method: "POST",
        headers: {
          cookie: `gms_access=access; gms_csrf=${csrf}`,
          origin: "https://app.example.com",
          "x-csrf-token": csrf,
          "content-type": "application/json",
          ...(idempotencyKey ? { "idempotency-key": idempotencyKey } : {}),
        },
        body: JSON.stringify({ display_location: "Front desk" }),
      });
    const response = await authenticatedMutation(
      mutation(),
      "/api/v1/check-in-challenges/",
      "POST",
    );
    expect(response.status).toBe(200);
    const headers = new Headers(
      (fetchMock.mock.calls.at(-1)?.[1] as RequestInit).headers,
    );
    expect(headers.get("Idempotency-Key")).toBeNull();

    const supplied = "f52030c1-18af-4e18-a1ec-50997d892ef4";
    expect(
      (
        await authenticatedMutation(
          mutation(supplied),
          "/api/v1/check-in-challenges/",
          "POST",
        )
      ).status,
    ).toBe(200);
    const forwarded = new Headers(
      (fetchMock.mock.calls.at(-1)?.[1] as RequestInit).headers,
    );
    expect(forwarded.get("Idempotency-Key")).toBe(supplied);
  });
});

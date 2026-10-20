import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { allowsPhase5Route, phase5Idempotency } from "@/lib/api/phase5-routes";
import { authenticatedMutation } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";

describe("phase 5 route allowlist", () => {
  it("accepts published list filters and rejects the generic scan route", () => {
    expect(
      allowsPhase5Route(
        "/api/v1/pt-bookings/",
        "?view=calendar&date_from=2026-10-19&date_to=2026-10-25&limit=25",
        "GET",
      ),
    ).toBe(true);
    expect(
      allowsPhase5Route(
        "/api/v1/pt-bookings/",
        "?view=calendar&date_from=2026-10-19&date_to=2026-12-19",
        "GET",
      ),
    ).toBe(false);
    expect(
      allowsPhase5Route("/api/v1/trainer-assignments/", "?gym_id=other", "GET"),
    ).toBe(false);
    expect(allowsPhase5Route("/api/v1/check-ins/scan/", "", "POST")).toBeNull();
    expect(
      allowsPhase5Route("/api/v1/trainer/check-ins/scan/", "", "POST"),
    ).toBe(true);
    expect(phase5Idempotency("/api/v1/trainer/check-ins/scan/", "POST")).toBe(
      "rejected",
    );
    expect(phase5Idempotency("/api/v1/pt-sales/", "POST")).toBe("required");
    expect(phase5Idempotency("/api/v1/class-types/", "POST")).toBe("none");
    expect(phase5Idempotency(`/api/v1/class-schedules/${ID}/`, "PATCH")).toBe(
      "required",
    );
  });
});

describe("trainer scan BFF", () => {
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

  it("forwards trainer scan without an idempotency key and blocks the generic route", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { id: ID } }),
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
        body: JSON.stringify({
          member_id: ID,
          payload: "challenge-payload",
        }),
      });

    expect(
      (
        await authenticatedMutation(
          mutation(),
          "/api/v1/trainer/check-ins/scan/",
          "POST",
        )
      ).status,
    ).toBe(200);
    const headers = new Headers(
      (fetchMock.mock.calls.at(-1)?.[1] as RequestInit).headers,
    );
    expect(headers.get("Idempotency-Key")).toBeNull();
    expect(
      JSON.parse(
        String((fetchMock.mock.calls.at(-1)?.[1] as RequestInit).body),
      ),
    ).toEqual({
      member_id: ID,
      payload: "challenge-payload",
    });

    expect(
      (
        await authenticatedMutation(
          mutation(ID),
          "/api/v1/trainer/check-ins/scan/",
          "POST",
        )
      ).status,
    ).toBe(400);
    expect(
      (
        await authenticatedMutation(
          mutation(),
          "/api/v1/check-ins/scan/",
          "POST",
        )
      ).status,
    ).toBe(503);
  });
});

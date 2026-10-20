import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authenticatedGet } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

describe("trainer dashboard BFF", () => {
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

  it("forwards the trainer dashboard and session report reads", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { timezone: "Asia/Bangkok" } }),
      );
    const request = new NextRequest(
      "https://app.example.com/api/operations/trainer/dashboard?range=today",
      { headers: { cookie: "gms_access=access" } },
    );

    const dashboard = await authenticatedGet(
      request,
      "/api/v1/trainer/dashboard/?range=today",
    );
    const report = await authenticatedGet(
      request,
      "/api/v1/trainer/reports/sessions/?date_from=2026-10-01&date_to=2026-10-03",
    );

    expect(dashboard.status).toBe(200);
    expect(report.status).toBe(200);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "https://api.example.com/api/v1/trainer/dashboard/?range=today",
      "https://api.example.com/api/v1/trainer/reports/sessions/?date_from=2026-10-01&date_to=2026-10-03",
    ]);
  });

  it("does not forward an extra trainer dashboard filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const request = new NextRequest(
      "https://app.example.com/api/operations/trainer/dashboard?range=today&gym_id=secret-gym",
      { headers: { cookie: "gms_access=access" } },
    );

    const blocked = await authenticatedGet(
      request,
      "/api/v1/trainer/dashboard/?range=today&gym_id=secret-gym",
    );

    expect(blocked.status).toBe(503);
    expect(await blocked.text()).not.toContain("secret-gym");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

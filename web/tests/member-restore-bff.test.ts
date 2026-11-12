import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/operations/[...path]/route";
import { resetEnvForTests } from "@/lib/env";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";

describe("member restore BFF", () => {
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

  it("forwards a member restore through the operations route", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { id: ID, status: "ACTIVE" } }),
      );
    const csrf = "x".repeat(43);
    const request = new NextRequest(
      `https://app.example.com/api/operations/members/${ID}/restore/`,
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

    const response = await POST(request, {
      params: Promise.resolve({ path: ["members", ID, "restore"] }),
    });

    expect(response.status).toBe(200);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      `/api/v1/members/${ID}/restore/`,
    );
  });
});

import { act, render } from "@testing-library/react";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionKeepalive } from "@/features/auth/session-keepalive";
import { resetEnvForTests } from "@/lib/env";
import { proxy } from "@/proxy";

const EIGHT_MINUTES = 8 * 60 * 1000;

describe("staff session refresh", () => {
  beforeEach(() => {
    process.env.DJANGO_API_URL = "https://api.example.com";
    process.env.APP_ORIGINS = "https://app.example.com";
    process.env.ACCESS_EXPIRED_CODE = "ACCESS_TOKEN_EXPIRED";
    process.env.BFF_SHARED_SECRET = "test-bff-shared-secret-32-chars-min";
    process.env.ALLOW_INSECURE_LOCALHOST_COOKIES = "false";
    resetEnvForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    resetEnvForTests();
  });

  it("rotates tokens before the ten-minute access token expires", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { csrf_token: "c".repeat(32) } }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    render(<SessionKeepalive />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EIGHT_MINUTES);
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/refresh",
      expect.objectContaining({ method: "POST", body: "{}" }),
    );
  });

  it("restores an expired page session during internal navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          data: {
            access_token: "new-access",
            refresh_token: "new-refresh",
            access_expires_at: new Date(Date.now() + 600_000).toISOString(),
            refresh_expires_at: new Date(
              Date.now() + 2_592_000_000,
            ).toISOString(),
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const request = new NextRequest("https://app.example.com/dashboard", {
      headers: { Cookie: "gms_refresh=old-refresh" },
    });

    const response = await proxy(request);

    expect(response.cookies.get("gms_access")?.value).toBe("new-access");
    expect(response.cookies.get("gms_refresh")?.value).toBe("new-refresh");
    expect(response.headers.get("x-middleware-request-cookie")).toContain(
      "gms_access=new-access",
    );
  });

  it("deduplicates simultaneous refreshes for the same session", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          data: {
            access_token: "shared-access",
            refresh_token: "shared-refresh",
            access_expires_at: new Date(Date.now() + 600_000).toISOString(),
            refresh_expires_at: new Date(
              Date.now() + 2_592_000_000,
            ).toISOString(),
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const request = () =>
      new NextRequest("https://app.example.com/dashboard", {
        headers: { Cookie: "gms_refresh=parallel-refresh" },
      });

    const [first, second] = await Promise.all([
      proxy(request()),
      proxy(request()),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first.cookies.get("gms_access")?.value).toBe("shared-access");
    expect(second.cookies.get("gms_access")?.value).toBe("shared-access");
  });
});

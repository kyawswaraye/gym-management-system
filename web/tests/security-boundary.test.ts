import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authenticatedGet,
  authMutation,
  stripCredentials,
} from "@/lib/api/server";
import { POST as selectGym } from "@/app/api/auth/select-gym/route";
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

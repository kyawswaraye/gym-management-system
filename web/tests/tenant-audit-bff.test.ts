import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as tenantAuditRoute from "@/app/api/operations/audit-logs/route";
import { authenticatedGet } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

const itemId = "34485f4c-d7df-4244-bae6-072868cc111a";
const actorId = "11111111-1111-4111-8111-111111111111";
const targetId = "f52030c1-18af-4e18-a1ec-50997d892ef4";
const cursor = "cursor.1:abc=";

const secrets = [
  "hunter2-password",
  "client-secret-value",
  "api-credential-value",
  "cred-value",
  "hidden-user@example.test",
  "Hidden@example.test",
  "gym-should-not-echo",
  "203.0.113.55",
  "198.51.100.14",
  "SecretBrowser/9",
  "super-secret-access-token",
  "super-secret-refresh-token",
  "invite-secret-token",
  "nested-token-secret",
  "refresh-session-secret",
  "selection-ticket-secret",
  "mfa-challenge-secret",
  "unexpected-debug-payload",
  "raw-query-password=hunter2-password",
  "raw-row-hunter2-password",
  "actor-user-should-hide",
  "rotated-access-token",
  "rotated-refresh-token",
];

const expectedPage = {
  success: true,
  data: [
    {
      id: itemId,
      actor_membership_id: actorId,
      impersonator_membership_id: null,
      action: "ROLE_CREATED",
      module: "roles",
      target_type: "Role",
      target_id: targetId,
      old_values: {
        note: "kept-note",
        password: "[REDACTED]",
        client_secret: "[REDACTED]",
        refresh_session: "[REDACTED]",
        nested: {
          apiCredential: "[REDACTED]",
          EmailAddress: "[REDACTED]",
          email: "[REDACTED]",
          gym_id: "[REDACTED]",
          ip: "[REDACTED]",
          ip_address: "[REDACTED]",
          user_agent: "[REDACTED]",
          access_token: "[REDACTED]",
          gym_selection_ticket: "[REDACTED]",
          mfa_challenge_token: "[REDACTED]",
          actor_user_id: "[REDACTED]",
          safe: "visible-label",
        },
        items: [{ token: "[REDACTED]", label: "row-label" }],
      },
      new_values: {
        description: "participation stays",
        credential: "[REDACTED]",
        refresh_token: "[REDACTED]",
        invitation_token: "[REDACTED]",
      },
      request_id: "req_test",
      outcome: "SUCCESS",
      occurred_at: "2026-10-17T00:00:00Z",
    },
  ],
  meta: { has_more: true, next_after: cursor, scope: "SELF" },
  message: "Success",
};

function poisonedAuditBody() {
  return {
    success: true,
    data: [
      "raw-row-hunter2-password",
      {
        id: itemId,
        actor_membership_id: actorId,
        impersonator_membership_id: null,
        action: "ROLE_CREATED",
        module: "roles",
        target_type: "Role",
        target_id: targetId,
        old_values: {
          note: "kept-note",
          password: "hunter2-password",
          client_secret: "client-secret-value",
          refresh_session: "refresh-session-secret",
          nested: {
            apiCredential: "api-credential-value",
            EmailAddress: "Hidden@example.test",
            email: "hidden-user@example.test",
            gym_id: "gym-should-not-echo",
            ip: "198.51.100.14",
            ip_address: "203.0.113.55",
            user_agent: "SecretBrowser/9",
            access_token: "super-secret-access-token",
            gym_selection_ticket: "selection-ticket-secret",
            mfa_challenge_token: "mfa-challenge-secret",
            actor_user_id: "actor-user-should-hide",
            safe: "visible-label",
          },
          items: [{ token: "nested-token-secret", label: "row-label" }],
        },
        new_values: {
          description: "participation stays",
          credential: "cred-value",
          refresh_token: "super-secret-refresh-token",
          invitation_token: "invite-secret-token",
        },
        request_id: "req_test",
        outcome: "SUCCESS",
        occurred_at: "2026-10-17T00:00:00Z",
        email: "hidden-user@example.test",
        gym_id: "gym-should-not-echo",
        ip_address: "203.0.113.55",
        user_agent: "SecretBrowser/9",
        password: "hunter2-password",
        access_token: "super-secret-access-token",
        actor_user_id: "actor-user-should-hide",
        leaked_query: "raw-query-password=hunter2-password",
      },
    ],
    meta: {
      has_more: true,
      next_after: cursor,
      scope: "SELF",
      email: "hidden-user@example.test",
      gym_id: "gym-should-not-echo",
      ip_address: "203.0.113.55",
      user_agent: "SecretBrowser/9",
    },
    message: "Success",
    refresh_token: "super-secret-refresh-token",
    password: "hunter2-password",
    email: "hidden-user@example.test",
    gym_id: "gym-should-not-echo",
    ip_address: "203.0.113.55",
    user_agent: "SecretBrowser/9",
    debug_body: "unexpected-debug-payload",
  };
}

function expectSecretsAbsent(body: string, extra: string[] = []) {
  for (const secret of [...secrets, ...extra]) {
    expect(body).not.toContain(secret);
  }
}

function auditRequest(cookie: string) {
  return new NextRequest(
    "https://app.example.com/api/operations/audit-logs?module=roles&limit=25",
    { headers: { cookie } },
  );
}

describe("tenant audit BFF response hardening", () => {
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

  it("redacts a first-attempt tenant audit response before serialization", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(poisonedAuditBody()));

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store, private");
    expect(JSON.parse(body)).toEqual(expectedPage);
    expect(body).toContain("kept-note");
    expect(body).toContain("visible-label");
    expect(body).toContain("participation stays");
    expect(body).toContain("row-label");
    expect(body).toContain("ROLE_CREATED");
    expect(body).toContain(cursor);
    expectSecretsAbsent(body, ["browser-access"]);
    expect(response.headers.getSetCookie().join("\n")).not.toContain(
      "hunter2-password",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://api.example.com/api/v1/audit-logs/?module=roles&limit=25",
    );
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer browser-access",
    );
  });

  it("omits a plain-secret success message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        ...poisonedAuditBody(),
        message: "hunter2-password",
      }),
    );

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(JSON.parse(body)).toEqual({
      success: expectedPage.success,
      data: expectedPage.data,
      meta: expectedPage.meta,
    });
    expect(body).not.toContain("hunter2-password");
    expectSecretsAbsent(body);
  });

  it("redacts the refresh-retry response and still rotates cookies", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: {
              code: "ACCESS_TOKEN_EXPIRED",
              message: "Expired hidden-user@example.test",
              fields: { password: ["hunter2-password"] },
            },
            refresh_token: "super-secret-refresh-token",
            debug_body: "unexpected-debug-payload",
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "rotated-access-token",
            access_expires_at: "2026-10-17T01:00:00Z",
            refresh_token: "rotated-refresh-token",
            refresh_expires_at: "2026-11-17T00:00:00Z",
          },
        }),
      )
      .mockResolvedValueOnce(Response.json(poisonedAuditBody()));
    const csrf = "x".repeat(43);
    const response = await tenantAuditRoute.GET(
      new NextRequest(
        "https://app.example.com/api/operations/audit-logs?module=roles&limit=25",
        {
          headers: {
            cookie: `gms_access=old-access; gms_refresh=old-refresh; gms_csrf=${csrf}`,
            origin: "https://app.example.com",
            "x-csrf-token": csrf,
          },
        },
      ),
    );
    const body = await response.text();
    const setCookie = response.headers.getSetCookie().join("\n");
    const [, refreshInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    const [retryUrl, retryInit] = fetchMock.mock.calls[2] as [
      string,
      RequestInit,
    ];

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "https://api.example.com/api/v1/auth/refresh/",
    );
    expect(JSON.parse(String(refreshInit.body))).toEqual({
      refresh_token: "old-refresh",
    });
    expect(retryUrl).toBe(
      "https://api.example.com/api/v1/audit-logs/?module=roles&limit=25",
    );
    expect(new Headers(retryInit.headers).get("authorization")).toBe(
      "Bearer rotated-access-token",
    );
    expect(JSON.parse(body)).toEqual(expectedPage);
    expectSecretsAbsent(body, ["old-access", "old-refresh"]);
    expect(setCookie).toContain("gms_access=rotated-access-token");
    expect(setCookie).toContain("gms_refresh=rotated-refresh-token");
    expect(setCookie).toContain("HttpOnly");
    expect(setCookie).not.toContain("hunter2-password");
    expect(setCookie).not.toContain("super-secret-refresh-token");
    expect(setCookie).not.toContain("hidden-user@example.test");
  });

  it("keeps a validated error code and request id without echoing secrets", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          success: false,
          error: {
            code: "PERMISSION_DENIED",
            message: "hunter2-password",
            fields: {
              password: ["hunter2-password"],
              client_secret: "client-secret-value",
              apiCredential: ["api-credential-value"],
              email: ["hidden-user@example.test"],
              gym_id: "gym-should-not-echo",
              ip_address: "203.0.113.55",
              user_agent: "SecretBrowser/9",
              token: "super-secret-access-token",
              refresh_session: "refresh-session-secret",
              note: "hunter2-password",
              action: ["client-secret-value"],
              detail: ["raw-query-password=hunter2-password"],
            },
            raw_query: "raw-query-password=hunter2-password",
          },
          request_id: "req_denied",
          password: "hunter2-password",
          email: "hidden-user@example.test",
          debug: "unexpected-debug-payload",
        },
        { status: 403 },
      ),
    );

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(403);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "PERMISSION_DENIED",
        message: "The request could not be completed.",
      },
      request_id: "req_denied",
    });
    expect(body).toContain("PERMISSION_DENIED");
    expect(body).toContain("req_denied");
    expect(body).not.toContain("fields");
    expectSecretsAbsent(body);
  });

  it("does not echo a poisoned expired body when refresh cannot run", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          success: false,
          error: {
            code: "ACCESS_TOKEN_EXPIRED",
            message: "hunter2-password",
            fields: {
              password: ["hunter2-password"],
              note: "hunter2-password",
            },
          },
          email: "hidden-user@example.test",
          refresh_token: "super-secret-refresh-token",
          request_id: "hunter2-password leaked",
          debug_body: "unexpected-debug-payload",
        },
        { status: 401 },
      ),
    );

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(401);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "ACCESS_TOKEN_EXPIRED",
        message: "The request could not be completed.",
      },
    });
    expect(body).not.toContain("fields");
    expectSecretsAbsent(body);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fail-closes a malformed success payload with a non-2xx response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        password: "hunter2-password",
        email: "hidden-user@example.test",
        note: "hunter2-password",
        query: "raw-query-password=hunter2-password",
        debug_body: "unexpected-debug-payload",
      }),
    );

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
    expectSecretsAbsent(body);
  });

  it("fail-closes a 2xx error envelope instead of returning HTTP 200", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        success: false,
        error: {
          code: "PERMISSION_DENIED",
          message: "hunter2-password",
          fields: { note: "hunter2-password" },
        },
        request_id: "req_denied",
      }),
    );

    const response = await tenantAuditRoute.GET(
      auditRequest("gms_access=browser-access"),
    );
    const body = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
    expect(body).not.toContain("PERMISSION_DENIED");
    expect(body).not.toContain("req_denied");
    expectSecretsAbsent(body);
  });

  it("still rotates cookies when the refreshed audit payload is malformed", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: {
              code: "ACCESS_TOKEN_EXPIRED",
              message: "hunter2-password",
              fields: { note: "hunter2-password" },
            },
          },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            access_token: "rotated-access-token",
            access_expires_at: "2026-10-17T01:00:00Z",
            refresh_token: "rotated-refresh-token",
            refresh_expires_at: "2026-11-17T00:00:00Z",
          },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          success: false,
          error: {
            code: "PERMISSION_DENIED",
            message: "hunter2-password",
            fields: { note: "hunter2-password" },
          },
          password: "hunter2-password",
        }),
      );
    const csrf = "x".repeat(43);
    const response = await tenantAuditRoute.GET(
      new NextRequest(
        "https://app.example.com/api/operations/audit-logs?module=roles&limit=25",
        {
          headers: {
            cookie: `gms_access=old-access; gms_refresh=old-refresh; gms_csrf=${csrf}`,
            origin: "https://app.example.com",
            "x-csrf-token": csrf,
          },
        },
      ),
    );
    const body = await response.text();
    const setCookie = response.headers.getSetCookie().join("\n");

    expect(response.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The request could not be completed.",
      },
    });
    expectSecretsAbsent(body, ["old-access", "old-refresh"]);
    expect(setCookie).toContain("gms_access=rotated-access-token");
    expect(setCookie).toContain("gms_refresh=rotated-refresh-token");
    expect(setCookie).toContain("HttpOnly");
    expect(setCookie).not.toContain("hunter2-password");
  });

  it("fail-closes unknown tenant audit queries without echoing them", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const response = await tenantAuditRoute.GET(
      new NextRequest(
        "https://app.example.com/api/operations/audit-logs?password=hunter2-password&email=hidden-user%40example.test&gym_id=gym-should-not-echo",
        { headers: { cookie: "gms_access=browser-access" } },
      ),
    );
    const body = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(body)).toEqual({
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "The service is temporarily unavailable. Please try again.",
      },
    });
    expectSecretsAbsent(body);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves platform audit on the generic response path", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        success: true,
        data: [
          {
            id: itemId,
            action: "GYM_CREATED",
            actor_email: "owner@example.test",
          },
        ],
        meta: { has_more: false, next_after: null },
        message: "Success",
      }),
    );
    const response = await authenticatedGet(
      new NextRequest(
        "https://app.example.com/api/operations/admin/audit-logs",
        {
          headers: { cookie: "gms_access=browser-access" },
        },
      ),
      `/api/v1/admin/audit-logs/?target_gym_id=${targetId}`,
    );
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(JSON.parse(body)).toEqual({
      success: true,
      data: [
        {
          id: itemId,
          action: "GYM_CREATED",
          actor_email: "owner@example.test",
        },
      ],
      meta: { has_more: false, next_after: null },
      message: "Success",
    });
    expect(body).toContain("owner@example.test");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  logBffFailure,
  normalizedRoute,
  redactValue,
  resolveRequestId,
} from "@/lib/log/structured";

describe("structured BFF logs", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps a valid request id and replaces everything else", () => {
    expect(resolveRequestId("req_abc-1.2:3")).toBe("req_abc-1.2:3");
    expect(resolveRequestId("bad id")).toMatch(/^req_[0-9a-f-]{36}$/);
    expect(resolveRequestId("x".repeat(129))).toMatch(/^req_/);
  });

  it("normalizes ids and drops the query string", () => {
    expect(
      normalizedRoute(
        "/api/v1/members/39f07d2b-bbd8-43d2-9c18-e4cbf3af131e/?token=secret",
      ),
    ).toBe("/api/v1/members/{id}/");
    expect(normalizedRoute("/reset?token=abc")).toBe("/reset");
    expect(normalizedRoute(`/${"a".repeat(200)}`)).toBe("unmatched");
  });

  it("redacts nested secrets, signatures, bodies, and control characters", () => {
    const redacted = redactValue({
      request_id: "req_keep",
      route: "/api/v1/auth/login/",
      authorization: "Bearer eyJhbGciOi.secret",
      cookie: "access=secret",
      nested: {
        access_token: "access-secret",
        refresh_token: "refresh-secret",
        gym_selection_ticket: "ticket-secret",
        mfa_challenge_token: "mfa-secret",
        reset_token: "reset-secret",
        invitation_token: "invite-secret",
        bff_shared_secret: "bff-secret",
        minio_secret_key: "provider-secret",
        body: { password: "hidden" },
      },
      note: "see https://files.example/obj?X-Amz-Signature=abc123&X-Amz-Credential=cred\u0000",
    }) as {
      request_id: string;
      authorization: string;
      nested: Record<string, string>;
      note: string;
    };

    expect(redacted.request_id).toBe("req_keep");
    expect(redacted.authorization).toBe("[redacted]");
    expect(redacted.nested.access_token).toBe("[redacted]");
    expect(redacted.nested.refresh_token).toBe("[redacted]");
    expect(redacted.nested.gym_selection_ticket).toBe("[redacted]");
    expect(redacted.nested.mfa_challenge_token).toBe("[redacted]");
    expect(redacted.nested.reset_token).toBe("[redacted]");
    expect(redacted.nested.invitation_token).toBe("[redacted]");
    expect(redacted.nested.bff_shared_secret).toBe("[redacted]");
    expect(redacted.nested.minio_secret_key).toBe("[redacted]");
    expect(redacted.nested.body).toBe("[redacted]");
    expect(redacted.note).not.toContain("abc123");
    expect(redacted.note).not.toContain("\u0000");
    expect(JSON.stringify(redacted)).not.toContain("ticket-secret");
  });

  it("logs an auth failure without the credential", () => {
    const lines: string[] = [];
    vi.spyOn(console, "log").mockImplementation((line: unknown) => {
      lines.push(String(line));
    });
    logBffFailure({
      requestId: "req_auth",
      route: "/api/v1/auth/login/?ticket=plain-ticket",
      method: "POST",
      status: 401,
      outcome: "AUTHENTICATION_FAILED",
    });
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('"request_id":"req_auth"');
    expect(lines[0]).toContain('"/api/v1/auth/login/"');
    expect(lines[0]).not.toContain("plain-ticket");
    expect(lines[0]).not.toContain("\n");
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import { listAuditLogs, OperationError } from "@/features/operations/client";
import type { TenantAuditItem } from "@/types/api";

const targetId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

const item: TenantAuditItem = {
  id: "34485f4c-d7df-4244-bae6-072868cc111a",
  actor_membership_id: "11111111-1111-4111-8111-111111111111",
  impersonator_membership_id: null,
  action: "ROLE_CREATED",
  module: "roles",
  target_type: "Role",
  target_id: targetId,
  old_values: { note: "before", password: "hunter2" },
  new_values: { note: "after" },
  request_id: "req_test",
  outcome: "SUCCESS",
  occurred_at: "2026-10-17T00:00:00Z",
};

describe("tenant audit browser client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calls the same-origin audit route and drops forbidden fields", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: [
            {
              ...item,
              actor_email: "hidden@example.test",
              gym_id: "gym-should-not-show",
              ip_address: "203.0.113.10",
              user_agent: "SecretBrowser/1.0",
              access_token: "super-secret-token",
              old_values: {
                note: "before",
                password: "hunter2",
                nested: {
                  refresh_token: "refresh-secret",
                  email: "hidden@example.test",
                },
              },
            },
          ],
          meta: { has_more: true, next_after: "cursor.1:abc", scope: "SELF" },
          message: "Success",
          refresh_token: "secret-refresh",
        }),
      );

    const page = await listAuditLogs({
      action: "ROLE_CREATED",
      module: "roles",
      outcome: "SUCCESS",
      targetType: "Role",
      targetId: targetId.toUpperCase(),
      limit: "25",
      after: "cursor.1:abc",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "https://app.example");
    expect(parsed.pathname).toBe("/api/operations/audit-logs");
    expect(parsed.searchParams.get("target_id")).toBe(targetId);
    expect(parsed.searchParams.get("action")).toBe("ROLE_CREATED");
    expect(parsed.searchParams.get("module")).toBe("roles");
    expect(parsed.searchParams.get("outcome")).toBe("SUCCESS");
    expect(parsed.searchParams.get("target_type")).toBe("Role");
    expect(parsed.searchParams.get("limit")).toBe("25");
    expect(parsed.searchParams.get("after")).toBe("cursor.1:abc");
    expect(parsed.searchParams.get("target_gym_id")).toBeNull();
    expect(parsed.searchParams.get("email")).toBeNull();
    expect(parsed.searchParams.get("token")).toBeNull();
    expect(init?.method).toBeUndefined();
    expect(init?.credentials).toBe("same-origin");
    expect(new Headers(init?.headers).has("authorization")).toBe(false);
    expect(page.meta).toEqual({
      has_more: true,
      next_after: "cursor.1:abc",
      scope: "SELF",
    });
    expect(page.data[0]?.old_values).toEqual({
      note: "before",
      password: "[REDACTED]",
      nested: { refresh_token: "[REDACTED]", email: "[REDACTED]" },
    });
    expect(Object.keys(page.data[0] ?? {}).sort()).toEqual(
      [
        "action",
        "actor_membership_id",
        "id",
        "impersonator_membership_id",
        "module",
        "new_values",
        "occurred_at",
        "old_values",
        "outcome",
        "request_id",
        "target_id",
        "target_type",
      ].sort(),
    );
    const serialized = JSON.stringify(page);
    expect(serialized).not.toContain("hidden@example.test");
    expect(serialized).not.toContain("gym-should-not-show");
    expect(serialized).not.toContain("203.0.113.10");
    expect(serialized).not.toContain("SecretBrowser/1.0");
    expect(serialized).not.toContain("super-secret-token");
    expect(serialized).not.toContain("hunter2");
    expect(serialized).not.toContain("refresh-secret");
    expect(serialized).not.toContain("secret-refresh");
  });

  it("rejects an invalid page or cursor before fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(
      listAuditLogs({
        action: "",
        module: "",
        outcome: "",
        targetType: "",
        targetId: "",
        limit: "101",
      }),
    ).rejects.toBeInstanceOf(OperationError);
    await expect(
      listAuditLogs({
        action: "",
        module: "",
        outcome: "MAYBE",
        targetType: "",
        targetId: "",
        limit: "25",
      }),
    ).rejects.toThrow("Choose a valid outcome.");
    await expect(
      listAuditLogs({
        action: "",
        module: "",
        outcome: "",
        targetType: "",
        targetId: "not-a-uuid",
        limit: "25",
        after: "cursor secret",
      }),
    ).rejects.toThrow("Enter a valid target ID.");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a response whose scope is not GYM or SELF", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      Response.json({
        success: true,
        data: [item],
        meta: { has_more: false, next_after: null, scope: "ALL" },
      }),
    );
    await expect(
      listAuditLogs({
        action: "",
        module: "",
        outcome: "",
        targetType: "",
        targetId: "",
        limit: "25",
      }),
    ).rejects.toThrow("The audit list could not be loaded.");
  });
});

import { describe, expect, it } from "vitest";

import {
  buildTenantAuditListPath,
  formatTenantAuditValue,
  isSensitiveAuditKey,
  redactTenantAuditValue,
  tenantAuditScopeLabel,
  type TenantAuditFilters,
} from "@/features/operations/audit-policy";

const filters = (
  overrides: Partial<TenantAuditFilters> = {},
): TenantAuditFilters => ({
  action: "",
  module: "",
  outcome: "",
  targetType: "",
  targetId: "",
  limit: "25",
  ...overrides,
});

describe("tenant audit list policy", () => {
  it("labels gym scope and self scope without treating self as gym history", () => {
    expect(tenantAuditScopeLabel("GYM")).toBe("All gym events");
    expect(tenantAuditScopeLabel("SELF")).toBe("Your actions only");
    expect(tenantAuditScopeLabel("OTHER" as "SELF")).toBe("Your actions only");
    expect(tenantAuditScopeLabel("OTHER" as "SELF")).not.toBe("All gym events");
  });

  it("builds only the seven allowlisted filters", () => {
    const targetId = "F52030C1-18AF-4E18-A1EC-50997D892EF4";
    const cursor = "cursor.1:abcDEF_12-zz";
    const built = buildTenantAuditListPath({
      ...filters({
        action: " ROLE_CREATED ",
        module: "roles",
        outcome: "DENIED",
        targetType: " Role ",
        targetId,
        limit: "10",
        after: cursor,
      }),
      email: "hidden@example.test",
      gymId: "gym-1",
      token: "secret-token",
      password: "hunter2",
    } as TenantAuditFilters);
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    const params = new URL(built.path, "https://app.example").searchParams;
    expect(built.path.startsWith("audit-logs?")).toBe(true);
    expect(built.path.startsWith("admin/")).toBe(false);
    expect(params.get("action")).toBe("ROLE_CREATED");
    expect(params.get("module")).toBe("roles");
    expect(params.get("outcome")).toBe("DENIED");
    expect(params.get("target_type")).toBe("Role");
    expect(params.get("target_id")).toBe(targetId.toLowerCase());
    expect(params.get("limit")).toBe("10");
    expect(params.get("after")).toBe(cursor);
    expect([...params.keys()]).toEqual([
      "action",
      "module",
      "outcome",
      "target_type",
      "target_id",
      "limit",
      "after",
    ]);
    expect(params.get("email")).toBeNull();
    expect(params.get("gym_id")).toBeNull();
    expect(params.get("target_gym_id")).toBeNull();
    expect(params.get("token")).toBeNull();
    expect(params.get("password")).toBeNull();
    expect(params.get("ip_address")).toBeNull();
    expect(params.get("user_agent")).toBeNull();
  });

  it("rejects malformed filters and omits blank text", () => {
    expect(buildTenantAuditListPath(filters()).ok).toBe(true);
    const blank = buildTenantAuditListPath(
      filters({ action: "   ", module: " ", targetType: " " }),
    );
    expect(blank.ok).toBe(true);
    if (blank.ok) {
      expect([
        ...new URL(blank.path, "https://app.example").searchParams.keys(),
      ]).toEqual(["limit"]);
    }

    expect(
      buildTenantAuditListPath(filters({ targetId: "not-a-uuid" })).ok,
    ).toBe(false);
    expect(buildTenantAuditListPath(filters({ outcome: "MAYBE" })).ok).toBe(
      false,
    );
    expect(buildTenantAuditListPath(filters({ outcome: "success" })).ok).toBe(
      false,
    );
    expect(
      buildTenantAuditListPath(filters({ action: "A".repeat(101) })).ok,
    ).toBe(false);
    expect(
      buildTenantAuditListPath(filters({ module: "M".repeat(51) })).ok,
    ).toBe(false);
    expect(
      buildTenantAuditListPath(filters({ targetType: "T".repeat(101) })).ok,
    ).toBe(false);
    expect(
      buildTenantAuditListPath(filters({ module: "M".repeat(50) })).ok,
    ).toBe(true);
    expect(buildTenantAuditListPath(filters({ limit: "0" })).ok).toBe(false);
    expect(buildTenantAuditListPath(filters({ limit: "01" })).ok).toBe(false);
    expect(buildTenantAuditListPath(filters({ limit: "101" })).ok).toBe(false);
    expect(buildTenantAuditListPath(filters({ limit: "" })).ok).toBe(false);
    expect(buildTenantAuditListPath(filters({ limit: "1" })).ok).toBe(true);
    expect(buildTenantAuditListPath(filters({ limit: "100" })).ok).toBe(true);
    expect(
      buildTenantAuditListPath(filters({ after: "cursor secret" })).ok,
    ).toBe(false);
    expect(
      buildTenantAuditListPath(filters({ after: "a".repeat(2049) })).ok,
    ).toBe(false);
    expect(
      buildTenantAuditListPath(filters({ after: "a".repeat(2048) })).ok,
    ).toBe(true);
  });

  it("redacts nested credential and identity keys before formatting", () => {
    const redacted = redactTenantAuditValue({
      note: "kept-note",
      description: "participation stays",
      target_id: "34485f4c-d7df-4244-bae6-072868cc111a",
      access_token: "super-secret-token",
      nested: {
        password: "hunter2",
        refresh_token: "refresh-secret",
        client_secret: "secret-value",
        credential: "cred-value",
        email: "hidden@example.test",
        actor_email: "actor@example.test",
        actor_user_id: "user-1",
        gym_id: "gym-should-not-show",
        ip_address: "203.0.113.10",
        user_agent: "SecretBrowser/1.0",
        safe: "visible-label",
      },
      items: [{ token: "nested-secret", label: "row-label" }],
    });
    expect(redacted).toEqual({
      note: "kept-note",
      description: "participation stays",
      target_id: "34485f4c-d7df-4244-bae6-072868cc111a",
      access_token: "[REDACTED]",
      nested: {
        password: "[REDACTED]",
        refresh_token: "[REDACTED]",
        client_secret: "[REDACTED]",
        credential: "[REDACTED]",
        email: "[REDACTED]",
        actor_email: "[REDACTED]",
        actor_user_id: "[REDACTED]",
        gym_id: "[REDACTED]",
        ip_address: "[REDACTED]",
        user_agent: "[REDACTED]",
        safe: "visible-label",
      },
      items: [{ token: "[REDACTED]", label: "row-label" }],
    });
    expect(isSensitiveAuditKey("description")).toBe(false);
    expect(isSensitiveAuditKey("target_id")).toBe(false);
    expect(isSensitiveAuditKey("ip")).toBe(true);
    expect(isSensitiveAuditKey("user-agent")).toBe(true);
    const formatted = formatTenantAuditValue({
      password: "hunter2",
      note: "<script>alert(1)</script>",
    });
    expect(formatted).toContain("[REDACTED]");
    expect(formatted).toContain("<script>alert(1)</script>");
    expect(formatted).not.toContain("hunter2");
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(formatTenantAuditValue(circular)).toBe("Unavailable");
  });
});

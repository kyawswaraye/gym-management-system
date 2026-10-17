import { describe, expect, it } from "vitest";

import {
  buildAuditListPath,
  formatAuditValue,
  redactAuditValue,
} from "@/features/platform/audit-policy";
import {
  buildSubscriptionUpdate,
  normalizePlanPrice,
} from "@/features/platform/subscription-policy";
import type { PlatformSubscription } from "@/types/api";

const plan: PlatformSubscription = {
  id: "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
  code: "GROWTH",
  name: "Growth",
  status: "ACTIVE",
  price: "10.00",
  currency: "THB",
  billing_interval: "MONTHLY",
};

describe("subscription update policy", () => {
  it("normalizes a nonnegative price to 2 decimals and rejects unsafe values", () => {
    expect(normalizePlanPrice("10")).toBe("10.00");
    expect(normalizePlanPrice("10.5")).toBe("10.50");
    expect(normalizePlanPrice("0")).toBe("0.00");
    expect(normalizePlanPrice("0.10")).toBe("0.10");
    expect(normalizePlanPrice("-1")).toBeNull();
    expect(normalizePlanPrice("10.555")).toBeNull();
    expect(normalizePlanPrice("01.00")).toBeNull();
    expect(normalizePlanPrice("1e2")).toBeNull();
    expect(normalizePlanPrice("")).toBeNull();
  });

  it("sends only changed writable fields", () => {
    expect(
      buildSubscriptionUpdate(plan, {
        name: "Growth Plus",
        price: "10",
        billingInterval: "MONTHLY",
        status: "ACTIVE",
      }),
    ).toEqual({ ok: true, body: { name: "Growth Plus" } });

    expect(
      buildSubscriptionUpdate(plan, {
        name: "Growth",
        price: "12.5",
        billingInterval: "YEARLY",
        status: "RETIRED",
      }),
    ).toEqual({
      ok: true,
      body: {
        price: "12.50",
        billing_interval: "YEARLY",
        status: "RETIRED",
      },
    });
  });

  it("rejects blank, lowercase, and unchanged updates", () => {
    expect(
      buildSubscriptionUpdate(plan, {
        name: " ",
        price: "10.00",
        billingInterval: "MONTHLY",
        status: "ACTIVE",
      }).ok,
    ).toBe(false);
    expect(
      buildSubscriptionUpdate(plan, {
        name: "Growth",
        price: "10.00",
        billingInterval: "monthly",
        status: "ACTIVE",
      }),
    ).toMatchObject({ ok: false });
    const unchanged = buildSubscriptionUpdate(plan, {
      name: "Growth",
      price: "10.00",
      billingInterval: "MONTHLY",
      status: "ACTIVE",
    });
    expect(unchanged.ok).toBe(false);
    if (!unchanged.ok) {
      expect(unchanged.message).toBe("Change at least one field.");
    }
  });
});

describe("audit list policy", () => {
  it("builds a bounded query and rejects unsafe filters", () => {
    const gymId = "F52030C1-18AF-4E18-A1EC-50997D892EF4";
    const built = buildAuditListPath({
      targetGymId: gymId,
      action: "SUBSCRIPTION_PLAN_UPDATED",
      outcome: "DENIED",
      limit: "10",
      after: "cursor.1:abc",
    });
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    const params = new URL(built.path, "https://app.example").searchParams;
    expect(params.get("target_gym_id")).toBe(gymId.toLowerCase());
    expect(params.get("action")).toBe("SUBSCRIPTION_PLAN_UPDATED");
    expect(params.get("outcome")).toBe("DENIED");
    expect(params.get("limit")).toBe("10");
    expect(params.get("after")).toBe("cursor.1:abc");
    expect([...params.keys()]).toEqual([
      "target_gym_id",
      "action",
      "outcome",
      "limit",
      "after",
    ]);
    expect(params.get("access_token")).toBeNull();
    expect(params.get("token")).toBeNull();

    expect(
      buildAuditListPath({
        targetGymId: "not-a-uuid",
        action: "",
        outcome: "",
        limit: "25",
      }).ok,
    ).toBe(false);
    expect(
      buildAuditListPath({
        targetGymId: "",
        action: "",
        outcome: "MAYBE",
        limit: "25",
      }).ok,
    ).toBe(false);
    expect(
      buildAuditListPath({
        targetGymId: "",
        action: "A".repeat(101),
        outcome: "",
        limit: "25",
      }).ok,
    ).toBe(false);
    expect(
      buildAuditListPath({
        targetGymId: "",
        action: "",
        outcome: "",
        limit: "101",
      }).ok,
    ).toBe(false);
    expect(
      buildAuditListPath({
        targetGymId: "",
        action: "",
        outcome: "",
        limit: "25",
        after: "cursor with spaces",
      }).ok,
    ).toBe(false);
  });

  it("redacts credential-like audit values before display", () => {
    expect(
      redactAuditValue({
        name: "Growth",
        access_token: "super-secret-token",
        nested: { refresh_token: "refresh-secret", note: "kept" },
      }),
    ).toEqual({
      name: "Growth",
      access_token: "[REDACTED]",
      nested: { refresh_token: "[REDACTED]", note: "kept" },
    });
    expect(formatAuditValue({ password: "hunter2" })).toContain("[REDACTED]");
    expect(formatAuditValue({ password: "hunter2" })).not.toContain("hunter2");
  });
});

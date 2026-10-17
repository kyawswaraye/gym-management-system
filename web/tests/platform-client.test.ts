import { beforeEach, describe, expect, it, vi } from "vitest";

import { getCsrfToken } from "@/features/auth/client";
import { provisionGym } from "@/features/platform/client";
import type { GymProvisionRequest } from "@/types/api";

vi.mock("@/features/auth/client", () => ({
  getCsrfToken: vi.fn(async () => "csrf-token"),
  mutationHeaders: (token: string) => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  }),
}));

const request: GymProvisionRequest = {
  gym: {
    code: "YGN-02",
    name: "Second Gym",
    timezone: "Asia/Yangon",
    currency: "THB",
    subscription_plan_id: "11111111-1111-4111-8111-111111111111",
    subscription_status: "TRIAL",
  },
  owner: {
    email: "owner@example.test",
    first_name: "Ada",
    last_name: "Owner",
    send_invitation: true,
  },
};

describe("platform provisioning client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("posts same-origin with CSRF and a stable idempotency key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        success: true,
        data: { gym: { code: "YGN-02" } },
        message: "Gym provisioned",
      }),
    );
    const key = "11111111-1111-4111-8111-111111111111";

    const result = await provisionGym(request, key);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];

    expect(getCsrfToken).toHaveBeenCalledTimes(1);
    expect(url).toBe("/api/operations/admin/gyms");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(key);
    expect(new Headers(init.headers).has("authorization")).toBe(false);
    expect(JSON.parse(String(init.body))).toEqual(request);
    expect(JSON.stringify(init)).not.toMatch(
      /access_token|refresh_token|Bearer|invitation_token/,
    );
    expect(result).toEqual({ gym: { code: "YGN-02" } });
  });
});

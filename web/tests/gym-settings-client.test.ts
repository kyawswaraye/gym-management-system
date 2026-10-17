import { beforeEach, describe, expect, it, vi } from "vitest";

import { getCsrfToken } from "@/features/auth/client";
import {
  getGymProfile,
  getGymSettings,
  updateGymProfile,
  updateGymSettings,
} from "@/features/operations/client";

vi.mock("@/features/auth/client", () => ({
  getCsrfToken: vi.fn(async () => "csrf-token"),
  mutationHeaders: (token: string) => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  }),
}));

describe("gym settings browser client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses same-origin BFF routes and never sends a bearer token", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({
          success: true,
          data: {
            name: "Yangon Fitness",
            week_starts_on: 1,
          },
        }),
      );

    await getGymProfile();
    await getGymSettings();
    await updateGymProfile({ name: "Updated Gym" });
    await updateGymSettings({
      week_starts_on: 0,
      require_separate_finance_approval: true,
    });

    expect(getCsrfToken).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.map(([url, init]) => [url, init?.method]),
    ).toEqual([
      ["/api/operations/gym", undefined],
      ["/api/operations/settings", undefined],
      ["/api/operations/gym", "PATCH"],
      ["/api/operations/settings", "PATCH"],
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      expect(JSON.stringify(init ?? {})).not.toMatch(
        /access_token|refresh_token/,
      );
    }
    const patch = fetchMock.mock.calls[2]?.[1];
    expect(new Headers(patch?.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(JSON.parse(String(patch?.body))).toEqual({ name: "Updated Gym" });
  });
});

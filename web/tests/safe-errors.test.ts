import { describe, expect, it } from "vitest";

import { safeErrorResponse } from "@/lib/api/errors";

describe("safe browser errors", () => {
  it("returns a bounded envelope without exception details", async () => {
    const response = safeErrorResponse(
      503,
      "UPSTREAM_UNAVAILABLE",
      "Please try again.",
      "req_123",
    );
    const text = await response.text();

    expect(response.headers.get("cache-control")).toBe("no-store, private");
    expect(JSON.parse(text)).toEqual({
      success: false,
      error: {
        code: "UPSTREAM_UNAVAILABLE",
        message: "Please try again.",
      },
      request_id: "req_123",
    });
    expect(text).not.toContain("stack");
    expect(text).not.toContain("token");
  });
});

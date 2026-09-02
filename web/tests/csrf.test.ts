import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { verifyMutationRequest } from "@/lib/auth/csrf";

const token = "a".repeat(43);
const allowed = new Set(["https://app.example.com"]);

function request(headers: HeadersInit = {}) {
  return new NextRequest("https://app.example.com/api/auth/login", {
    method: "POST",
    headers: {
      cookie: `gms_csrf=${token}`,
      "x-csrf-token": token,
      ...headers,
    },
  });
}

describe("mutation provenance", () => {
  it("accepts an exact allowlisted Origin", () => {
    expect(
      verifyMutationRequest(
        request({ origin: "https://app.example.com" }),
        allowed,
      ),
    ).toEqual({ ok: true });
  });

  it("uses an exact Referer origin only when Origin is absent", () => {
    expect(
      verifyMutationRequest(
        request({ referer: "https://app.example.com/login" }),
        allowed,
      ),
    ).toEqual({ ok: true });
  });

  it.each([
    [{ origin: "https://evil.example" }, "origin"],
    [{ referer: "https://evil.example/login" }, "referer"],
    [{ origin: "" }, "provenance"],
    [{ origin: "https://app.example.com", "x-csrf-token": "wrong" }, "CSRF"],
  ])("rejects an invalid mutation", (headers, message) => {
    const result = verifyMutationRequest(request(headers), allowed);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain(message);
  });
});

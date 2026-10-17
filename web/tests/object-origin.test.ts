import { describe, expect, it } from "vitest";

import { isConfiguredObjectUrl } from "@/lib/object-origin";

const CONFIGURED = [
  "https://files.example.com",
  "http://localhost:8088",
  "http://files.localhost:8088",
  "http://evil.example",
  "not a url",
  "https://files.example.com/bucket",
].join(",");

describe("object origin allowlist", () => {
  it("allows configured HTTPS origins and explicit localhost HTTP outside production", () => {
    expect(
      isConfiguredObjectUrl(
        "https://files.example.com/gym-private",
        "test",
        CONFIGURED,
      ),
    ).toBe(true);
    expect(
      isConfiguredObjectUrl(
        "http://localhost:8088/gym-private",
        "development",
        CONFIGURED,
      ),
    ).toBe(true);
    expect(
      isConfiguredObjectUrl(
        "http://files.localhost:8088/gym-private",
        "test",
        CONFIGURED,
      ),
    ).toBe(true);
    expect(
      isConfiguredObjectUrl(
        "http://127.0.0.1:9000/gym-private",
        "test",
        "http://127.0.0.1:9000",
      ),
    ).toBe(true);
  });

  it("rejects unexpected hosts, protocols, credentials, and production HTTP", () => {
    expect(
      isConfiguredObjectUrl(
        "https://files.example.com.evil.com/gym-private",
        "production",
        CONFIGURED,
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "http://evil.example/gym-private",
        "development",
        CONFIGURED,
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "http://localhost:8088/gym-private",
        "production",
        CONFIGURED,
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "http://files.localhost:8088/gym-private",
        "production",
        CONFIGURED,
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "http://files.localhost:8088/gym-private",
        "production",
        CONFIGURED,
        "1",
      ),
    ).toBe(true);
    expect(
      isConfiguredObjectUrl(
        "http://localhost:8088/gym-private",
        "production",
        CONFIGURED,
        "1",
      ),
    ).toBe(true);
    expect(
      isConfiguredObjectUrl(
        "http://evil.example/gym-private",
        "production",
        CONFIGURED,
        "1",
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "https://user:pass@files.example.com/gym-private",
        "production",
        CONFIGURED,
      ),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl("javascript:alert(1)", "test", CONFIGURED),
    ).toBe(false);
    expect(
      isConfiguredObjectUrl(
        "https://other.example/gym-private",
        "production",
        CONFIGURED,
      ),
    ).toBe(false);
  });
});

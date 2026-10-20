import { assertRuntimeEnv, parseEnv } from "./env";

describe("environment config", () => {
  it("allows debug cleartext only for the local API hosts", () => {
    expect(
      parseEnv({ API_BASE_URL: "http://10.0.2.2:8000", APP_ENV: "debug" })
        .apiBaseUrl,
    ).toBe("http://10.0.2.2:8000");
    expect(
      parseEnv({ API_BASE_URL: "http://localhost:8000", APP_ENV: "debug" })
        .apiBaseUrl,
    ).toBe("http://localhost:8000");
    expect(() =>
      parseEnv({ API_BASE_URL: "http://example.com", APP_ENV: "debug" }),
    ).toThrow(/local/);
  });

  it("requires HTTPS for release", () => {
    expect(
      parseEnv({ API_BASE_URL: "https://api.example.com", APP_ENV: "release" })
        .appEnv,
    ).toBe("release");
    expect(() =>
      parseEnv({ API_BASE_URL: "http://10.0.2.2:8000", APP_ENV: "release" }),
    ).toThrow(/HTTPS/);
    expect(() =>
      assertRuntimeEnv(
        { apiBaseUrl: "http://10.0.2.2:8000", appEnv: "debug" },
        false,
      ),
    ).toThrow(/release/);
  });
});

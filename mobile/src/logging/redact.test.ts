import { redactSecrets } from "./redact";

describe("secret redaction", () => {
  it("removes tokens, QR payloads, and presigned URLs from diagnostics", () => {
    expect(
      redactSecrets({
        access_token: "access",
        refresh_token: "refresh",
        payload: "gms-checkin:v1:secret",
        push_token: "fcm",
        nested: { url: "https://signed.example/photo" },
        title: "Holiday hours",
      }),
    ).toEqual({
      access_token: "[redacted]",
      refresh_token: "[redacted]",
      payload: "[redacted]",
      push_token: "[redacted]",
      nested: { url: "[redacted]" },
      title: "Holiday hours",
    });
  });
});

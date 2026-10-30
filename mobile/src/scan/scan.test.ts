import { scanErrorMessage } from "./errors";
import { readCheckInPayload } from "./payload";

describe("check-in QR handling", () => {
  it("accepts a gym check-in payload and rejects other codes", () => {
    expect(readCheckInPayload(" gms-checkin:v1:abc123 ")).toEqual({
      ok: true,
      payload: "gms-checkin:v1:abc123",
    });
    expect(readCheckInPayload("https://example.test")).toEqual({
      ok: false,
      reason: "unrecognized",
    });
    expect(readCheckInPayload("")).toEqual({ ok: false, reason: "empty" });
    expect(readCheckInPayload("gms-checkin:v1:has space")).toEqual({
      ok: false,
      reason: "unrecognized",
    });
  });

  it("maps duplicate and invalid scans without echoing the payload", () => {
    expect(scanErrorMessage("CHECK_IN_DUPLICATE", "fallback")).toMatch(
      /already checked in/i,
    );
    expect(scanErrorMessage("CHECK_IN_CODE_INVALID", "fallback")).toMatch(
      /expired or changed/i,
    );
    expect(scanErrorMessage("CHECK_IN_OPEN", "fallback")).toMatch(
      /already checked in/i,
    );
    expect(scanErrorMessage("CHECK_IN_INELIGIBLE", "fallback")).toMatch(
      /not eligible/i,
    );
    expect(scanErrorMessage("OTHER", "fallback")).toBe("fallback");
    expect(
      scanErrorMessage("CHECK_IN_DUPLICATE", "gms-checkin:v1:secret"),
    ).not.toContain("gms-checkin");
  });
});

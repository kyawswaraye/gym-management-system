import { describe, expect, it } from "vitest";

import { formatInstant, zonedLocalToIso } from "@/lib/date";

describe("gym timezone instants", () => {
  it("converts a Yangon wall time to UTC", () => {
    expect(zonedLocalToIso("2026-06-02T10:00", "Asia/Yangon")).toBe(
      "2026-06-02T03:30:00.000Z",
    );
  });

  it("rejects a time that is not a local datetime", () => {
    expect(() =>
      zonedLocalToIso("2026-06-02T10:00:00Z", "Asia/Yangon"),
    ).toThrow("Enter a valid time.");
  });

  it("formats a missing instant as not recorded", () => {
    expect(formatInstant(null, "Asia/Yangon")).toBe("Not recorded");
  });
});

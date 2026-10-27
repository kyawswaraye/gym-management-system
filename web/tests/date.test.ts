import { describe, expect, it } from "vitest";

import {
  formatAppDate,
  formatAppDateTime,
  formatInstant,
  zonedLocalToIso,
} from "@/lib/date";

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

  it("shows calendar dates and instants as day-month-year", () => {
    expect(formatAppDate("2026-10-28")).toBe("28-10-2026");
    expect(formatAppDate("not-a-date")).toBe("not-a-date");
    expect(formatAppDateTime("2026-10-28T02:30:00.000Z", "Asia/Yangon")).toBe(
      "28-10-2026 09:00",
    );
    expect(formatInstant("2026-10-28T02:30:00.000Z", "Asia/Yangon")).toBe(
      "28-10-2026 09:00",
    );
  });
});

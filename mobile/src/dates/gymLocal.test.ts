import {
  formatGymDate,
  formatGymDateTime,
  formatRemainingSessions,
  formatServerMoney,
  scheduleWindow,
} from "./gymLocal";

describe("gym-local presentation", () => {
  it("formats calendar dates and offset datetimes without shifting the server day", () => {
    expect(formatGymDate("2026-10-20")).toBe("20 Oct 2026");
    expect(formatGymDateTime("2026-10-20T23:30:00+07:00")).toBe(
      "20 Oct 2026 23:30 +07:00",
    );
  });

  it("uses a 42-day inclusive window", () => {
    expect(scheduleWindow({ year: 2026, month: 10, day: 20 })).toEqual({
      date_from: "2026-10-20",
      date_to: "2026-11-30",
    });
  });

  it("shows server money and remaining sessions unchanged", () => {
    expect(formatServerMoney("10.50", "THB")).toBe("THB 10.50");
    expect(formatServerMoney(null, "THB")).toBe("Unavailable");
    expect(formatRemainingSessions(3)).toBe("3");
  });
});

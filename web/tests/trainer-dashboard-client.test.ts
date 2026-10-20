import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperationError } from "@/features/operations/client";
import {
  getTrainerDashboard,
  getTrainerSessionReport,
} from "@/features/pt/client";
import type { DashboardRangePreset, TrainerDashboard } from "@/types/api";

const dashboard: TrainerDashboard = {
  timezone: "Asia/Bangkok",
  today: "2026-10-20",
  range: { date_from: "2026-10-20", date_to: "2026-10-20" },
  generated_at: "2026-10-20T01:00:00Z",
  trainer_staff_profile_id: "11111111-1111-4111-8111-111111111111",
  active_assigned_members: 1,
  sessions: {
    bookings: 1,
    unique_members: 1,
    booked_sessions: 1,
    attended_sessions: 0,
    no_show_sessions: 0,
    cancelled_sessions: 0,
  },
  upcoming_bookings: [],
};

function json(data: unknown) {
  return { success: true, data, message: "Success" };
}

describe("trainer dashboard client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the preset dashboard and the bounded session report", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(json(dashboard)));

    await expect(getTrainerDashboard({ range: "this_month" })).resolves.toEqual(
      dashboard,
    );
    await expect(
      getTrainerDashboard({ date_from: "2024-01-01", date_to: "2024-12-31" }),
    ).resolves.toEqual(dashboard);
    await expect(
      getTrainerSessionReport({
        date_from: "2026-10-01",
        date_to: "2026-10-03",
      }),
    ).resolves.toEqual(dashboard);

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/operations/trainer/dashboard/?range=this_month",
      "/api/operations/trainer/dashboard/?date_from=2024-01-01&date_to=2024-12-31",
      "/api/operations/trainer/reports/sessions/?date_from=2026-10-01&date_to=2026-10-03",
    ]);
  });

  it("rejects an unknown preset and an unbounded range before fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    expect(() =>
      getTrainerDashboard({ range: "yesterday" as DashboardRangePreset }),
    ).toThrow(OperationError);
    expect(() =>
      getTrainerDashboard({ date_from: "2023-02-29", date_to: "2023-02-29" }),
    ).toThrow("Enter a valid date.");
    expect(() =>
      getTrainerSessionReport({
        date_from: "2026-10-02",
        date_to: "2026-10-01",
      }),
    ).toThrow("The end date cannot be before the start date.");
    expect(() =>
      getTrainerSessionReport({
        date_from: "2024-01-01",
        date_to: "2025-01-01",
      }),
    ).toThrow("A report range cannot exceed 366 days.");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

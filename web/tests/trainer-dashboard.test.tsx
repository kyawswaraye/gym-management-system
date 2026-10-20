import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getTrainerDashboard,
  getTrainerSessionReport,
} from "@/features/pt/client";
import { TrainerDashboardManager } from "@/features/pt/trainer-dashboard";
import type { TrainerDashboard, TrainerSessionReport } from "@/types/api";

vi.mock("@/features/pt/client", () => ({
  getTrainerDashboard: vi.fn(),
  getTrainerSessionReport: vi.fn(),
}));

const loadDashboard = vi.mocked(getTrainerDashboard);
const loadReport = vi.mocked(getTrainerSessionReport);

function counts(
  overrides: Partial<TrainerDashboard["sessions"]> = {},
): TrainerDashboard["sessions"] {
  return {
    bookings: 0,
    unique_members: 0,
    booked_sessions: 0,
    attended_sessions: 0,
    no_show_sessions: 0,
    cancelled_sessions: 0,
    ...overrides,
  };
}

function dashboard(
  upcoming: TrainerDashboard["upcoming_bookings"] = [],
): TrainerDashboard {
  return {
    timezone: "Asia/Bangkok",
    today: "2026-10-20",
    range: { date_from: "2026-10-20", date_to: "2026-10-20" },
    generated_at: "2026-10-20T01:00:00Z",
    trainer_staff_profile_id: "11111111-1111-4111-8111-111111111111",
    active_assigned_members: 3,
    sessions: counts({
      bookings: 4,
      unique_members: 2,
      booked_sessions: 1,
      attended_sessions: 2,
      no_show_sessions: 0,
      cancelled_sessions: 1,
    }),
    upcoming_bookings: upcoming,
  };
}

const report: TrainerSessionReport = {
  gym: {
    id: "44444444-4444-4444-8444-444444444444",
    code: "YGN",
    name: "Yangon Fitness",
  },
  timezone: "Asia/Bangkok",
  range: { date_from: "2026-10-01", date_to: "2026-10-02" },
  generated_at: "2026-10-20T02:00:00Z",
  source_status_policy: "current booking status",
  rows: [
    {
      date: "2026-10-01",
      ...counts({ bookings: 2, unique_members: 2, attended_sessions: 2 }),
    },
    {
      date: "2026-10-02",
      ...counts({ bookings: 3, unique_members: 3, booked_sessions: 3 }),
    },
  ],
  totals: counts({
    bookings: 5,
    unique_members: 4,
    booked_sessions: 3,
    attended_sessions: 2,
  }),
};

describe("trainer dashboard manager", () => {
  beforeEach(() => {
    loadDashboard.mockReset();
    loadReport.mockReset();
  });

  it("shows an accessible loading status before the dashboard arrives", () => {
    loadDashboard.mockReturnValue(new Promise(() => undefined));
    render(<TrainerDashboardManager />);
    expect(screen.getByText(/Loading trainer dashboard/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByText(/Choose a date range/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(
      screen.queryByRole("region", { name: "Session summary" }),
    ).not.toBeInTheDocument();
  });

  it("shows an accessible error and no summary when the dashboard fails", async () => {
    loadDashboard.mockRejectedValue(new Error("Dashboard unavailable."));
    render(<TrainerDashboardManager />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Dashboard unavailable.",
    );
    expect(
      screen.queryByRole("region", { name: "Session summary" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Upcoming sessions" }),
    ).not.toBeInTheDocument();
  });

  it("renders the trainer summary, upcoming sessions, and server totals", async () => {
    loadDashboard.mockResolvedValue(
      dashboard([
        {
          id: "33333333-3333-4333-8333-333333333333",
          member_id: "22222222-2222-4222-8222-222222222222",
          member_number: "M-0008",
          full_name: "Ada Lovelace",
          starts_at: "2026-10-20T03:00:00Z",
          ends_at: "2026-10-20T04:00:00Z",
        },
      ]),
    );
    loadReport.mockResolvedValue(report);
    render(<TrainerDashboardManager />);

    expect(
      await screen.findByRole("region", { name: "Session summary" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Active assigned members")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/Asia\/Bangkok/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-20 to 2026-10-20/)).toBeInTheDocument();
    expect(
      screen.getByText(/Generated 2026-10-20T01:00:00Z/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("11111111-1111-4111-8111-111111111111"),
    ).toBeInTheDocument();
    expect(screen.getByText("No-show sessions")).toBeInTheDocument();
    const upcoming = screen.getByRole("region", { name: "Upcoming sessions" });
    expect(
      within(upcoming).getByRole("rowheader", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(within(upcoming).getByText("M-0008")).toBeInTheDocument();
    expect(
      within(upcoming).getByText("2026-10-20T03:00:00Z"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("22222222-2222-4222-8222-222222222222"),
    ).not.toBeInTheDocument();
    expect(loadDashboard).toHaveBeenCalledWith({ range: "today" });

    fireEvent.click(screen.getByRole("button", { name: "This month" }));
    await waitFor(() => {
      expect(loadDashboard).toHaveBeenCalledWith({ range: "this_month" });
    });

    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-08-01" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-08-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply custom range" }));
    await waitFor(() => {
      expect(loadDashboard).toHaveBeenCalledWith({
        date_from: "2026-08-01",
        date_to: "2026-08-15",
      });
    });
    expect(screen.getByRole("button", { name: "This month" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-02" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Show session report" }),
    );
    expect(
      await screen.findByRole("region", { name: "Totals" }),
    ).toBeInTheDocument();
    const totals = within(screen.getByRole("region", { name: "Totals" }));
    expect(totals.getByText("Unique members").parentElement).toHaveTextContent(
      "4",
    );
    expect(totals.getByText("Bookings").parentElement).toHaveTextContent("5");
    expect(screen.getByText("Yangon Fitness")).toBeInTheDocument();
    expect(screen.getByText("YGN")).toBeInTheDocument();
    expect(screen.getByText("current booking status")).toBeInTheDocument();
    expect(
      screen.getByRole("rowheader", { name: "2026-10-01" }).closest("tr"),
    ).toHaveTextContent("0");
    expect(loadReport).toHaveBeenCalledWith({
      date_from: "2026-10-01",
      date_to: "2026-10-02",
    });
  });

  it("shows empty upcoming sessions and an empty report range", async () => {
    loadDashboard.mockResolvedValue(dashboard());
    loadReport.mockResolvedValue({ ...report, rows: [] });
    render(<TrainerDashboardManager />);
    expect(await screen.findByText("No upcoming sessions.")).toHaveAttribute(
      "role",
      "status",
    );

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-02" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Show session report" }),
    );
    expect(await screen.findByText(/No session days/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(
      screen.queryByRole("table", { name: "Session report rows" }),
    ).not.toBeInTheDocument();
  });

  it("shows a report loading status and an accessible report error", async () => {
    loadDashboard.mockResolvedValue(dashboard());
    let rejectReport: (error: Error) => void = () => undefined;
    loadReport.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectReport = reject;
      }),
    );
    render(<TrainerDashboardManager />);
    expect(
      await screen.findByText("Active assigned members"),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-02" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Show session report" }),
    );
    expect(screen.getByText(/Loading session report/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.queryByText(/Choose a date range/)).not.toBeInTheDocument();

    rejectReport(new Error("Report unavailable."));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Report unavailable.",
    );
    expect(
      screen.queryByRole("region", { name: "Totals" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Session summary" }),
    ).toBeInTheDocument();
  });
});

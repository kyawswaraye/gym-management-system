import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { platformGet } from "@/features/platform/client";
import { PlatformDashboard } from "@/features/platform/platform-dashboard";
import type { AdminDashboard } from "@/types/api";

vi.mock("@/features/platform/client", () => ({
  platformGet: vi.fn(),
}));

const getDashboard = vi.mocked(platformGet);

const dashboard: AdminDashboard = {
  gym_statuses: {
    total: 4,
    PENDING: 1,
    ACTIVE: 2,
    SUSPENDED: 1,
    CLOSED: 0,
  },
  gym_subscription_statuses: {
    TRIAL: 1,
    ACTIVE: 2,
    PAST_DUE: 1,
    CANCELLED: 0,
  },
  subscription_plan_statuses: {
    ACTIVE: 3,
    RETIRED: 1,
  },
};

describe("platform dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not request dashboard data without permission", async () => {
    render(<PlatformDashboard canView={false} />);

    expect(
      screen.getByText("You do not have access to the platform dashboard."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await waitFor(() => expect(getDashboard).not.toHaveBeenCalled());
  });

  it("renders the explicit status counts and no tenant business details", async () => {
    getDashboard.mockResolvedValue(dashboard);
    render(<PlatformDashboard canView />);

    expect(screen.getByText("Loading platform summary.")).toBeInTheDocument();
    const gyms = await screen.findByRole("table", { name: "Gym statuses" });
    const subscriptions = screen.getByRole("table", {
      name: "Gym subscription statuses",
    });
    const plans = screen.getByRole("table", {
      name: "Subscription plan statuses",
    });

    expect(getDashboard).toHaveBeenCalledTimes(1);
    expect(getDashboard).toHaveBeenCalledWith("admin/dashboard");
    expect(
      within(gyms).getByRole("row", { name: /^total\b/ }),
    ).toHaveTextContent("4");
    expect(
      within(gyms).getByRole("row", { name: /^PENDING\b/ }),
    ).toHaveTextContent("1");
    expect(
      within(gyms).getByRole("row", { name: /^ACTIVE\b/ }),
    ).toHaveTextContent("2");
    expect(
      within(gyms).getByRole("row", { name: /^SUSPENDED\b/ }),
    ).toHaveTextContent("1");
    expect(
      within(gyms).getByRole("row", { name: /^CLOSED\b/ }),
    ).toHaveTextContent("0");
    expect(
      within(subscriptions).getByRole("row", { name: /^TRIAL\b/ }),
    ).toHaveTextContent("1");
    expect(
      within(subscriptions).getByRole("row", { name: /^ACTIVE\b/ }),
    ).toHaveTextContent("2");
    expect(
      within(subscriptions).getByRole("row", { name: /^PAST_DUE\b/ }),
    ).toHaveTextContent("1");
    expect(
      within(subscriptions).getByRole("row", { name: /^CANCELLED\b/ }),
    ).toHaveTextContent("0");
    expect(
      within(plans).getByRole("row", { name: /^ACTIVE\b/ }),
    ).toHaveTextContent("3");
    expect(
      within(plans).getByRole("row", { name: /^RETIRED\b/ }),
    ).toHaveTextContent("1");
    expect(screen.queryByText(/revenue|member|@/i)).not.toBeInTheDocument();
  });

  it("shows an error without inventing counts", async () => {
    getDashboard.mockRejectedValue(new Error("Dashboard unavailable."));
    render(<PlatformDashboard canView />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Dashboard unavailable.",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

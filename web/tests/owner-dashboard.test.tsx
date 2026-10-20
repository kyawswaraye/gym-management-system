import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OwnerDashboard } from "@/features/operations/owner-dashboard";
import { operationGet } from "@/features/operations/client";
import type { DashboardData } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
}));

const loadDashboard = vi.mocked(operationGet);

function dashboard(financial: DashboardData["financial"]): DashboardData {
  return {
    range: { date_from: "2026-10-01", date_to: "2026-10-31" },
    timezone: "Asia/Bangkok",
    currency: "THB",
    operational: {
      active_members: 4,
      new_members: 2,
      expired_memberships: 1,
      check_ins: 9,
      unique_attendees: 6,
      currently_checked_in: 3,
      expiring_within_3_days: 1,
      expiring_within_7_days: 2,
      expiring_within_30_days: 5,
      pending_approvals: 8,
    },
    financial,
  };
}

const financial: NonNullable<DashboardData["financial"]> = {
  total_revenue: "10.00",
  new_member_revenue: "4.00",
  renewal_revenue: "0.00",
  pt_revenue: "0.00",
  class_revenue: "6.00",
  product_revenue: "0.00",
  other_revenue: "0.00",
  pending_payment_member_count: 1,
  pending_payment_amount: "2.00",
  revenue_sources_ranked: [
    { source: "CLASS", amount: "6.00" },
    { source: "NEW_MEMBER", amount: "4.00" },
    { source: "RENEWAL", amount: "0.00" },
    { source: "PT", amount: "0.00" },
    { source: "PRODUCT", amount: "0.00" },
    { source: "OTHER", amount: "0.00" },
  ],
  top_product_items: [],
  total_expenses: "3.00",
  expenses_by_category: [
    { system_key: "RENT", amount: "3.00" },
    { system_key: "UTILITIES", amount: "0.00" },
  ],
  staff_salary_expense: "0.00",
  pt_commission_expense: "0.00",
  net_profit: "1.00",
};

describe("owner dashboard", () => {
  beforeEach(() => {
    loadDashboard.mockReset();
  });

  it("shows an accessible loading status before the response arrives", () => {
    loadDashboard.mockReturnValue(new Promise(() => undefined));
    render(<OwnerDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading dashboard");
  });

  it("shows an accessible error and no totals when the request fails", async () => {
    loadDashboard.mockRejectedValue(new Error("Dashboard unavailable."));
    render(<OwnerDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Dashboard unavailable.",
    );
    expect(
      screen.queryByRole("region", { name: "Operational totals" }),
    ).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Financial totals" }),
    ).toBeNull();
  });

  it("renders operational totals and omits financial totals when financial is null", async () => {
    loadDashboard.mockResolvedValue(dashboard(null));
    render(<OwnerDashboard />);
    expect(
      await screen.findByRole("region", { name: "Operational totals" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Active members")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/Asia\/Bangkok/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-01 to 2026-10-31/)).toBeInTheDocument();
    expect(
      screen.getByText("Financial totals are not included in this response."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Financial totals" }),
    ).toBeNull();
    expect(screen.queryByText("Net profit")).toBeNull();
    expect(loadDashboard).toHaveBeenCalledWith("dashboard/?range=today");
  });

  it("renders server financial figures, including class, without recalculating profit", async () => {
    loadDashboard.mockResolvedValue(dashboard(financial));
    render(<OwnerDashboard />);
    expect(
      await screen.findByRole("region", { name: "Financial totals" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Class")).toBeInTheDocument();
    expect(screen.getByText("1.00 THB")).toBeInTheDocument();
    expect(screen.queryByText("7.00 THB")).toBeNull();
    expect(screen.getByText("RENT")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "This month" }));
    await waitFor(() => {
      expect(loadDashboard).toHaveBeenCalledWith("dashboard/?range=this_month");
    });

    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-08-01" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-08-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply custom range" }));
    await waitFor(() => {
      expect(loadDashboard).toHaveBeenCalledWith(
        "dashboard/?date_from=2026-08-01&date_to=2026-08-15",
      );
    });
  });
});

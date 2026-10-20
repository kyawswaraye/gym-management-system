import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReportsManager } from "@/features/reports/reports-manager";
import { downloadReportCsv } from "@/features/reports/download";
import { operationGet } from "@/features/operations/client";
import type {
  CashBookReport,
  CheckInReport,
  ClassReport,
  InventoryReport,
  MembershipExpiryReport,
  PeriodReport,
  ProductSalesReport,
  ProfitLossReport,
  PTSessionReport,
  StockMovementReport,
} from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
}));

vi.mock("@/features/reports/download", () => ({
  downloadReportCsv: vi.fn().mockResolvedValue(undefined),
}));

const loadReport = vi.mocked(operationGet);

function report(): PeriodReport {
  const row = {
    date: "2026-10-01",
    new_members: 1,
    expired_memberships: 0,
    check_ins: 1,
    unique_attendees: 1,
    new_member_revenue: "0.00",
    renewal_revenue: "0.00",
    pt_revenue: "0.00",
    class_revenue: "30.00",
    product_revenue: "20.00",
    other_revenue: "0.00",
    total_revenue: "50.00",
    product_quantity: "2.000",
    product_cogs: "8.00",
    product_gross_profit: "12.00",
    total_expenses: "47.00",
    net_profit: "3.00",
  };
  return {
    gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
    timezone: "Asia/Yangon",
    currency: "MMK",
    range: { date_from: "2026-10-01", date_to: "2026-10-01" },
    generated_at: "2026-10-19T06:00:00Z",
    source_status_policy: {
      revenue:
        "posted_and_void_charges_on_business_date__posted_refunds_and_reversals_on_correction_date",
      expenses:
        "posted_and_void_originals_on_business_date__posted_reversals_on_correction_date",
      operations: "authoritative_rows_on_gym_local_dates",
    },
    rows: [
      row,
      {
        ...row,
        date: "2026-10-02",
        unique_attendees: 1,
        total_revenue: "0.00",
      },
    ],
    totals: {
      ...row,
      unique_attendees: 1,
      staff_salary_expense: "0.00",
      pt_commission_expense: "0.00",
      expenses_by_category: [
        { system_key: "RENT", amount: "40.00" },
        { system_key: "STAFF_SALARY", amount: "0.00" },
      ],
    },
  };
}

describe("reports manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the server rows and range unique attendees", async () => {
    loadReport.mockResolvedValue(report());
    render(<ReportsManager />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Choose a date, month, or range.",
    );
    fireEvent.change(screen.getByLabelText("Date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show daily report" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith("reports/daily/?date=2026-10-01");
    });
    expect(
      screen.getByRole("heading", { name: "Daily report" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Yangon Fitness")).toBeInTheDocument();
    expect(screen.getAllByText("50.00").length).toBeGreaterThan(0);
    const totals = screen.getByRole("region", { name: "Totals" });
    const unique = within(totals).getByText("Unique attendees").closest("div");
    expect(unique?.querySelector("dd")).toHaveTextContent("1");
    expect(screen.getByText("RENT 40.00")).toBeInTheDocument();
  });

  it("shows the server error without inventing totals", async () => {
    loadReport.mockRejectedValue(new Error("Report unavailable."));
    render(<ReportsManager />);
    fireEvent.change(screen.getByLabelText("Year"), {
      target: { value: "2026" },
    });
    fireEvent.change(screen.getByLabelText("Month"), {
      target: { value: "10" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Show monthly report" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Report unavailable.",
    );
    expect(
      screen.queryByRole("region", { name: "Totals" }),
    ).not.toBeInTheDocument();
    expect(loadReport).toHaveBeenCalledWith(
      "reports/monthly/?year=2026&month=10",
    );
  });

  it("renders cash-book and profit/loss server rows", async () => {
    const cash: CashBookReport = {
      gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
      timezone: "Asia/Yangon",
      currency: "MMK",
      range: { date_from: "2026-10-01", date_to: "2026-10-02" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy: {
        sessions:
          "all_sessions_on_business_date__open_closed_and_void_counted_separately",
        transactions:
          "append_only_positive_amounts_grouped_by_source_type_and_direction",
      },
      rows: [
        {
          date: "2026-10-01",
          session_count: 2,
          open_session_count: 1,
          closed_session_count: 1,
          void_session_count: 0,
          opening_cash: "110.00",
          cash_in: "46.00",
          cash_out: "27.00",
          net_cash_flow: "19.00",
          payment_in: "40.00",
          payment_out: "5.00",
          expense_in: "2.00",
          expense_out: "10.00",
          commission_in: "1.00",
          commission_out: "7.00",
          manual_in: "3.00",
          manual_out: "1.00",
          reversal_in: "0.00",
          reversal_out: "4.00",
          expected_closing: "129.00",
          actual_closing: "120.50",
          difference: "1.50",
        },
      ],
      totals: {
        session_count: 2,
        open_session_count: 1,
        closed_session_count: 1,
        void_session_count: 0,
        opening_cash: "110.00",
        cash_in: "46.00",
        cash_out: "27.00",
        net_cash_flow: "19.00",
        payment_in: "40.00",
        payment_out: "5.00",
        expense_in: "2.00",
        expense_out: "10.00",
        commission_in: "1.00",
        commission_out: "7.00",
        manual_in: "3.00",
        manual_out: "1.00",
        reversal_in: "0.00",
        reversal_out: "4.00",
        expected_closing: "129.00",
        actual_closing: "120.50",
        difference: "1.50",
      },
    };
    loadReport.mockResolvedValueOnce(cash);
    render(<ReportsManager />);
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-02" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show cash book" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/cash-book/?date_from=2026-10-01&date_to=2026-10-02",
      );
    });
    const cashReport = screen.getByRole("region", { name: "Cash book" });
    const cashTotals = within(cashReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(cashTotals).getByText("Net cash flow").closest("div"),
    ).toHaveTextContent("19.00");

    const profit: ProfitLossReport = {
      gym: cash.gym,
      timezone: cash.timezone,
      currency: cash.currency,
      range: cash.range,
      generated_at: cash.generated_at,
      source_status_policy: report().source_status_policy,
      rows: [
        {
          date: "2026-10-01",
          new_member_revenue: "0.00",
          renewal_revenue: "0.00",
          pt_revenue: "0.00",
          class_revenue: "30.00",
          product_revenue: "0.00",
          other_revenue: "0.00",
          total_revenue: "30.00",
          product_cogs: "4.00",
          product_gross_profit: "6.00",
          total_expenses: "45.00",
          net_profit: "-15.00",
        },
      ],
      totals: {
        new_member_revenue: "0.00",
        renewal_revenue: "0.00",
        pt_revenue: "0.00",
        class_revenue: "30.00",
        product_revenue: "0.00",
        other_revenue: "0.00",
        total_revenue: "30.00",
        product_cogs: "4.00",
        product_gross_profit: "6.00",
        total_expenses: "45.00",
        net_profit: "-15.00",
        staff_salary_expense: "0.00",
        pt_commission_expense: "0.00",
        expenses_by_category: [{ system_key: "RENT", amount: "40.00" }],
      },
    };
    loadReport.mockResolvedValueOnce(profit);
    fireEvent.click(
      screen.getByRole("button", { name: "Show profit and loss" }),
    );
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/profit-loss/?date_from=2026-10-01&date_to=2026-10-02",
      );
    });
    const profitReport = screen.getByRole("region", {
      name: "Profit and loss",
    });
    const profitTotals = within(profitReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(profitTotals).getByText("Net profit").closest("div"),
    ).toHaveTextContent("-15.00");
    expect(within(profitReport).getByText("RENT 40.00")).toBeInTheDocument();
    expect(
      within(profitReport).queryByText("Product quantity"),
    ).not.toBeInTheDocument();

    const sales: ProductSalesReport = {
      gym: cash.gym,
      timezone: cash.timezone,
      currency: cash.currency,
      range: cash.range,
      generated_at: cash.generated_at,
      source_status_policy:
        "immutable_line_snapshots__not_current_product_cost",
      rows: [
        {
          product_id: "product-1",
          sku: "PRO",
          name: "Protein",
          is_active: true,
          quantity_sold: "1.000",
          revenue: "10.00",
          cogs: "4.00",
          gross_profit: "6.00",
        },
      ],
      totals: {
        quantity_sold: "2.000",
        revenue: "12.00",
        cogs: "5.00",
        gross_profit: "7.00",
        product_count: 4,
        active_product_count: 3,
      },
      meta: { next_after: "cursor-2", has_more: true },
    };
    const salesPage: ProductSalesReport = {
      ...sales,
      rows: [
        {
          product_id: "product-2",
          sku: "SHA",
          name: "Shaker",
          is_active: true,
          quantity_sold: "0.000",
          revenue: "0.00",
          cogs: "0.00",
          gross_profit: "0.00",
        },
      ],
      meta: { next_after: null, has_more: false },
    };
    loadReport.mockResolvedValueOnce(sales).mockResolvedValueOnce(salesPage);
    fireEvent.click(screen.getByRole("button", { name: "Show product sales" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/product-sales/?date_from=2026-10-01&date_to=2026-10-02&limit=25",
      );
    });
    const salesReport = screen.getByRole("region", { name: "Product sales" });
    expect(within(salesReport).getByText("MMK")).toBeInTheDocument();
    expect(
      within(salesReport).getByRole("rowheader", { name: "PRO" }),
    ).toBeInTheDocument();
    const salesTotals = within(salesReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(salesTotals).getByText("Revenue").closest("div"),
    ).toHaveTextContent("12.00");
    expect(salesReport).toHaveTextContent("More results: yes");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/product-sales/?date_from=2026-10-01&date_to=2026-10-02&limit=25&after=cursor-2",
      );
    });
    expect(
      within(screen.getByRole("region", { name: "Product sales" })).getByRole(
        "rowheader",
        { name: "SHA" },
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-03" },
    });
    expect(
      screen.queryByRole("region", { name: "Product sales" }),
    ).not.toBeInTheDocument();
  });

  it("hides financial controls and renders operational totals", async () => {
    const expiry: MembershipExpiryReport = {
      gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
      timezone: "Asia/Yangon",
      range: { date_from: "2026-10-01", date_to: "2026-10-03" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy:
        "expiry_date_and_current_status_at_generation__active_suspended_or_expired__not_historical_point_in_time_status",
      rows: [
        {
          date: "2026-10-01",
          expiring_memberships: 3,
          unique_members: 2,
          active_memberships: 1,
          suspended_memberships: 1,
          expired_memberships: 1,
        },
      ],
      totals: {
        expiring_memberships: 4,
        unique_members: 2,
        active_memberships: 1,
        suspended_memberships: 1,
        expired_memberships: 2,
      },
    };
    const visits: CheckInReport = {
      gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
      timezone: "Asia/Yangon",
      range: { date_from: "2026-10-01", date_to: "2026-10-03" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy:
        "currently_checked_in_is_all_same_gym_null_checkouts_independent_of_selected_range",
      rows: [
        {
          date: "2026-10-01",
          check_ins: 3,
          unique_attendees: 3,
          manual_check_ins: 0,
          qr_check_ins: 1,
          nfc_check_ins: 1,
          biometric_check_ins: 1,
          check_outs: 2,
          open_visits_from_day: 1,
        },
      ],
      totals: {
        check_ins: 3,
        unique_attendees: 3,
        manual_check_ins: 0,
        qr_check_ins: 1,
        nfc_check_ins: 1,
        biometric_check_ins: 1,
        check_outs: 3,
        open_visits_from_day: 1,
        currently_checked_in: 2,
      },
    };
    const sessions: PTSessionReport = {
      gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
      timezone: "Asia/Yangon",
      range: { date_from: "2026-10-01", date_to: "2026-10-03" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy: "not_status_transition_date_or_history",
      rows: [
        {
          date: "2026-10-01",
          bookings: 2,
          unique_members: 2,
          unique_trainers: 1,
          booked_sessions: 1,
          attended_sessions: 1,
          no_show_sessions: 0,
          cancelled_sessions: 0,
          metered_sessions: 1,
          unmetered_sessions: 1,
        },
      ],
      totals: {
        bookings: 2,
        unique_members: 1,
        unique_trainers: 1,
        booked_sessions: 1,
        attended_sessions: 1,
        no_show_sessions: 0,
        cancelled_sessions: 0,
        metered_sessions: 1,
        unmetered_sessions: 1,
      },
    };
    const classes: ClassReport = {
      gym: { id: "gym-1", code: "YGN", name: "Yangon Fitness" },
      timezone: "Asia/Yangon",
      range: { date_from: "2026-10-01", date_to: "2026-10-03" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy: "clamped_at_zero",
      rows: [
        {
          date: "2026-10-01",
          occurrences: 1,
          scheduled_occurrences: 1,
          completed_occurrences: 0,
          cancelled_occurrences: 0,
          available_capacity: 4,
          bookings: 2,
          unique_members: 2,
          booked_slots: 1,
          attended_slots: 1,
          no_show_slots: 0,
          cancelled_slots: 0,
          reserved_slots: 2,
          remaining_capacity: 2,
        },
      ],
      totals: {
        occurrences: 1,
        scheduled_occurrences: 1,
        completed_occurrences: 0,
        cancelled_occurrences: 0,
        available_capacity: 4,
        bookings: 2,
        unique_members: 2,
        unique_trainers: 1,
        booked_slots: 1,
        attended_slots: 1,
        no_show_slots: 0,
        cancelled_slots: 0,
        reserved_slots: 2,
        remaining_capacity: 0,
      },
    };
    loadReport
      .mockResolvedValueOnce(expiry)
      .mockResolvedValueOnce(visits)
      .mockResolvedValueOnce(sessions)
      .mockResolvedValueOnce(classes);
    render(<ReportsManager canViewFinancial={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("Choose a range.");
    expect(
      screen.queryByRole("button", { name: "Show daily report" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show cash book" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show product sales" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show stock movements" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show inventory" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-10-03" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Show membership expiry" }),
    );
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/membership-expiry/?date_from=2026-10-01&date_to=2026-10-03",
      );
    });
    const expiryReport = screen.getByRole("region", {
      name: "Membership expiry",
    });
    expect(expiryReport).toHaveTextContent(
      "not_historical_point_in_time_status",
    );
    expect(within(expiryReport).queryByText("MMK")).not.toBeInTheDocument();
    const expiryTotals = within(expiryReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(expiryTotals).getByText("Unique members").closest("div"),
    ).toHaveTextContent("2");
    fireEvent.click(screen.getByRole("button", { name: "Show check-ins" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/check-ins/?date_from=2026-10-01&date_to=2026-10-03",
      );
    });
    const visitReport = screen.getByRole("region", { name: "Check-ins" });
    const visitTotals = within(visitReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(visitTotals).getByText("Currently checked in").closest("div"),
    ).toHaveTextContent("2");
    expect(
      within(visitReport).getByRole("rowheader", { name: "2026-10-01" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show PT sessions" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/pt-sessions/?date_from=2026-10-01&date_to=2026-10-03",
      );
    });
    const sessionReport = screen.getByRole("region", { name: "PT sessions" });
    expect(sessionReport).toHaveTextContent(
      "not_status_transition_date_or_history",
    );
    expect(within(sessionReport).queryByText("MMK")).not.toBeInTheDocument();
    const sessionTotals = within(sessionReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(sessionTotals).getByText("Unique trainers").closest("div"),
    ).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: "Show classes" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/classes/?date_from=2026-10-01&date_to=2026-10-03",
      );
    });
    const classReport = screen.getByRole("region", { name: "Classes" });
    const classTotals = within(classReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(classTotals).getByText("Remaining capacity").closest("div"),
    ).toHaveTextContent("0");
    expect(
      within(classTotals).getByText("Unique trainers").closest("div"),
    ).toHaveTextContent("1");
    expect(within(classReport).queryByText("THB")).not.toBeInTheDocument();

    const stock: StockMovementReport = {
      gym: expiry.gym,
      timezone: expiry.timezone,
      range: expiry.range,
      generated_at: expiry.generated_at,
      source_status_policy: "stored_signed_quantity",
      rows: [
        {
          date: "2026-10-01",
          opening_quantity: "10.000",
          purchase_quantity: "0.000",
          sale_quantity: "-1.000",
          sale_return_quantity: "0.250",
          damage_quantity: "0.000",
          loss_quantity: "0.000",
          adjustment_quantity: "0.000",
          correction_quantity: "0.000",
          net_quantity: "9.250",
        },
      ],
      totals: {
        opening_quantity: "10.000",
        purchase_quantity: "0.000",
        sale_quantity: "-1.000",
        sale_return_quantity: "0.250",
        damage_quantity: "0.000",
        loss_quantity: "0.000",
        adjustment_quantity: "0.000",
        correction_quantity: "0.000",
        net_quantity: "9.250",
      },
    };
    const inventory: InventoryReport = {
      gym: expiry.gym,
      timezone: expiry.timezone,
      generated_at: expiry.generated_at,
      source_status_policy: "current_same_gym_movement_sum",
      rows: [
        {
          product_id: "product-1",
          category_code: "WATER",
          category_name: "Water",
          sku: "AAA",
          name: "Alpha",
          unit: "BTL",
          is_active: false,
          stock_quantity: "0.000",
          low_stock_threshold: "1.000",
          is_low_stock: true,
        },
      ],
      totals: {
        product_count: 3,
        active_product_count: 2,
        low_stock_product_count: 2,
        total_stock_quantity: "6.000",
      },
      meta: { next_after: null, has_more: false },
    };
    loadReport.mockResolvedValueOnce(stock).mockResolvedValueOnce(inventory);
    fireEvent.click(
      screen.getByRole("button", { name: "Show stock movements" }),
    );
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/stock-movements/?date_from=2026-10-01&date_to=2026-10-03",
      );
    });
    const stockReport = screen.getByRole("region", { name: "Stock movements" });
    expect(within(stockReport).queryByText("MMK")).not.toBeInTheDocument();
    expect(
      within(stockReport).getByRole("rowheader", { name: "2026-10-01" }),
    ).toBeInTheDocument();
    const stockTotals = within(stockReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(stockTotals).getByText("Net").closest("div"),
    ).toHaveTextContent("9.250");
    fireEvent.click(screen.getByRole("button", { name: "Show inventory" }));
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith("reports/inventory/?limit=25");
    });
    const inventoryReport = screen.getByRole("region", { name: "Inventory" });
    expect(within(inventoryReport).queryByText("MMK")).not.toBeInTheDocument();
    expect(
      within(inventoryReport).getByRole("rowheader", { name: "AAA" }),
    ).toBeInTheDocument();
    expect(within(inventoryReport).getByText("Archived")).toBeInTheDocument();
    expect(within(inventoryReport).getByText("Yes")).toBeInTheDocument();
    const inventoryTotals = within(inventoryReport).getByRole("region", {
      name: "Totals",
    });
    expect(
      within(inventoryTotals).getByText("Total stock").closest("div"),
    ).toHaveTextContent("6.000");
    expect(inventoryReport).toHaveTextContent("More results: no");
  });

  it("exports the loaded report and omits page cursors without permission", async () => {
    const download = vi.mocked(downloadReportCsv);
    loadReport.mockResolvedValue(report());
    render(<ReportsManager canExport={false} />);
    fireEvent.change(screen.getByLabelText("Date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show daily report" }));
    expect(
      await screen.findByRole("heading", { name: "Daily report" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Export CSV" }),
    ).not.toBeInTheDocument();

    render(<ReportsManager canExport />);
    fireEvent.change(screen.getAllByLabelText("Date")[1]!, {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Show daily report" })[1]!,
    );
    expect(
      await screen.findByRole("button", { name: "Export CSV" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => {
      expect(download).toHaveBeenCalledWith(
        "/api/operations/reports/daily/export/?date=2026-10-01",
      );
    });

    const sales: ProductSalesReport = {
      gym: report().gym,
      timezone: "Asia/Yangon",
      currency: "MMK",
      range: { date_from: "2026-10-01", date_to: "2026-10-04" },
      generated_at: "2026-10-19T06:00:00Z",
      source_status_policy: "immutable_line_snapshots",
      rows: [],
      totals: {
        quantity_sold: "0.000",
        revenue: "0.00",
        cogs: "0.00",
        gross_profit: "0.00",
        product_count: 0,
        active_product_count: 0,
      },
      meta: { next_after: "cursor-secret", has_more: true },
    };
    loadReport.mockResolvedValue(sales);
    fireEvent.change(screen.getAllByLabelText("From date")[1]!, {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getAllByLabelText("To date")[1]!, {
      target: { value: "2026-10-04" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Show product sales" })[1]!,
    );
    await waitFor(() => {
      expect(loadReport).toHaveBeenCalledWith(
        "reports/product-sales/?date_from=2026-10-01&date_to=2026-10-04&limit=25",
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => {
      expect(download).toHaveBeenCalledWith(
        "/api/operations/reports/product-sales/export/?date_from=2026-10-01&date_to=2026-10-04",
      );
    });
    expect(download.mock.calls.at(-1)?.[0]).not.toContain("after");
    expect(download.mock.calls.at(-1)?.[0]).not.toContain("limit");
  });
});

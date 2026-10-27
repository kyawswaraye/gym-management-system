"use client";
import { useTx } from "@/i18n/use-tx";

import { useState } from "react";

import { CursorControls } from "@/features/finance/ui";
import { operationGet } from "@/features/operations/client";
import { formatAppDate, formatAppDateTime } from "@/lib/date";
import { DigitInput } from "@/lib/ui/digit-input";
import { downloadReportCsv } from "@/features/reports/download";
import { IdentifierValue } from "@/lib/ui/identifier";
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
  ReportRow,
  StockMovementReport,
} from "@/types/api";

const REPORT_PAGE_LIMIT = "25";
const REPORT_PAGE_CAP = 20;

const COLUMNS: { key: keyof ReportRow; label: string }[] = [
  { key: "date", label: "Date" },
  { key: "new_members", label: "New members" },
  { key: "expired_memberships", label: "Expired memberships" },
  { key: "check_ins", label: "Check-ins" },
  { key: "unique_attendees", label: "Unique attendees" },
  { key: "new_member_revenue", label: "New member revenue" },
  { key: "renewal_revenue", label: "Renewal revenue" },
  { key: "pt_revenue", label: "PT revenue" },
  { key: "class_revenue", label: "Class revenue" },
  { key: "product_revenue", label: "Product revenue" },
  { key: "other_revenue", label: "Other revenue" },
  { key: "total_revenue", label: "Total revenue" },
  { key: "product_quantity", label: "Product quantity" },
  { key: "product_cogs", label: "Product COGS" },
  { key: "product_gross_profit", label: "Product gross profit" },
  { key: "total_expenses", label: "Total expenses" },
  { key: "net_profit", label: "Net profit" },
];

type TotalKey = Exclude<keyof PeriodReport["totals"], "expenses_by_category">;

const TOTAL_FIELDS: { key: TotalKey; label: string }[] = [
  { key: "new_members", label: "New members" },
  { key: "expired_memberships", label: "Expired memberships" },
  { key: "check_ins", label: "Check-ins" },
  { key: "unique_attendees", label: "Unique attendees" },
  { key: "new_member_revenue", label: "New member revenue" },
  { key: "renewal_revenue", label: "Renewal revenue" },
  { key: "pt_revenue", label: "PT revenue" },
  { key: "class_revenue", label: "Class revenue" },
  { key: "product_revenue", label: "Product revenue" },
  { key: "other_revenue", label: "Other revenue" },
  { key: "total_revenue", label: "Total revenue" },
  { key: "product_quantity", label: "Product quantity" },
  { key: "product_cogs", label: "Product COGS" },
  { key: "product_gross_profit", label: "Product gross profit" },
  { key: "total_expenses", label: "Total expenses" },
  { key: "net_profit", label: "Net profit" },
  { key: "staff_salary_expense", label: "Staff salary" },
  { key: "pt_commission_expense", label: "PT commission" },
];

type CashRow = CashBookReport["rows"][number];
type CashTotalKey = keyof CashBookReport["totals"];
type ProfitRow = ProfitLossReport["rows"][number];
type ProfitTotalKey = Exclude<
  keyof ProfitLossReport["totals"],
  "expenses_by_category"
>;

const CASH_COLUMNS: { key: keyof CashRow; label: string }[] = [
  { key: "date", label: "Date" },
  { key: "session_count", label: "Sessions" },
  { key: "open_session_count", label: "Open sessions" },
  { key: "closed_session_count", label: "Closed sessions" },
  { key: "void_session_count", label: "Void sessions" },
  { key: "opening_cash", label: "Opening cash" },
  { key: "cash_in", label: "Cash in" },
  { key: "cash_out", label: "Cash out" },
  { key: "net_cash_flow", label: "Net cash flow" },
  { key: "payment_in", label: "Payment in" },
  { key: "payment_out", label: "Payment out" },
  { key: "expense_in", label: "Expense in" },
  { key: "expense_out", label: "Expense out" },
  { key: "commission_in", label: "Commission in" },
  { key: "commission_out", label: "Commission out" },
  { key: "manual_in", label: "Manual in" },
  { key: "manual_out", label: "Manual out" },
  { key: "reversal_in", label: "Reversal in" },
  { key: "reversal_out", label: "Reversal out" },
  { key: "expected_closing", label: "Expected closing" },
  { key: "actual_closing", label: "Actual closing" },
  { key: "difference", label: "Difference" },
];

const CASH_TOTALS: { key: CashTotalKey; label: string }[] = CASH_COLUMNS.slice(
  1,
).map((column) => ({ key: column.key as CashTotalKey, label: column.label }));

const PROFIT_COLUMNS: { key: keyof ProfitRow; label: string }[] = [
  { key: "date", label: "Date" },
  { key: "new_member_revenue", label: "New member revenue" },
  { key: "renewal_revenue", label: "Renewal revenue" },
  { key: "pt_revenue", label: "PT revenue" },
  { key: "class_revenue", label: "Class revenue" },
  { key: "product_revenue", label: "Product revenue" },
  { key: "other_revenue", label: "Other revenue" },
  { key: "total_revenue", label: "Total revenue" },
  { key: "product_cogs", label: "Product COGS" },
  { key: "product_gross_profit", label: "Product gross profit" },
  { key: "total_expenses", label: "Total expenses" },
  { key: "net_profit", label: "Net profit" },
];

const PROFIT_TOTALS: { key: ProfitTotalKey; label: string }[] = [
  ...PROFIT_COLUMNS.slice(1).map((column) => ({
    key: column.key as ProfitTotalKey,
    label: column.label,
  })),
  { key: "staff_salary_expense", label: "Staff salary" },
  { key: "pt_commission_expense", label: "PT commission" },
];

type LoadedReport =
  | { kind: "daily" | "monthly"; report: PeriodReport }
  | { kind: "cash-book"; report: CashBookReport }
  | { kind: "profit-loss"; report: ProfitLossReport }
  | { kind: "membership-expiry"; report: MembershipExpiryReport }
  | { kind: "check-ins"; report: CheckInReport }
  | { kind: "pt-sessions"; report: PTSessionReport }
  | { kind: "classes"; report: ClassReport }
  | { kind: "product-sales"; report: ProductSalesReport }
  | { kind: "stock-movements"; report: StockMovementReport }
  | { kind: "inventory"; report: InventoryReport };

type ReportChoice = LoadedReport["kind"];

type Identity = {
  gym: { code: string; name: string };
  timezone: string;
  currency?: string;
  range?: { date_from: string; date_to: string };
  generated_at: string;
};

export function ReportsManager({
  canViewFinancial = true,
  canExport = false,
}: {
  canViewFinancial?: boolean;
  canExport?: boolean;
}) {
  const tx = useTx();
  const [date, setDate] = useState("");
  const [year, setYear] = useState("");
  const [month, setMonth] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedReport, setSelectedReport] = useState<ReportChoice>(
    canViewFinancial ? "daily" : "membership-expiry",
  );
  const [loaded, setLoaded] = useState<LoadedReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [productCursors, setProductCursors] = useState<string[]>([""]);
  const [productIndex, setProductIndex] = useState(0);
  const [inventoryCursors, setInventoryCursors] = useState<string[]>([""]);
  const [inventoryIndex, setInventoryIndex] = useState(0);

  function resetPagedReports() {
    setLoaded(null);
    setProductCursors([""]);
    setProductIndex(0);
    setInventoryCursors([""]);
    setInventoryIndex(0);
  }

  function productSalesPath(after = "") {
    const params = new URLSearchParams({
      date_from: dateFrom,
      date_to: dateTo,
      limit: REPORT_PAGE_LIMIT,
    });
    if (after) params.set("after", after);
    return `reports/product-sales/?${params.toString()}`;
  }

  function inventoryPath(after = "") {
    const params = new URLSearchParams({ limit: REPORT_PAGE_LIMIT });
    if (after) params.set("after", after);
    return `reports/inventory/?${params.toString()}`;
  }

  function showProductSales(after = "") {
    void load<ProductSalesReport>(productSalesPath(after), (report) => ({
      kind: "product-sales",
      report,
    }));
  }

  function showInventory(after = "") {
    void load<InventoryReport>(inventoryPath(after), (report) => ({
      kind: "inventory",
      report,
    }));
  }

  function nextProduct(after: string | null) {
    if (!after || productIndex + 1 >= REPORT_PAGE_CAP) return;
    if (productCursors.includes(after)) {
      setError(tx("This page cursor repeated. Refine the filters."));
      return;
    }
    setProductCursors((current) => [
      ...current.slice(0, productIndex + 1),
      after,
    ]);
    setProductIndex((index) => index + 1);
    showProductSales(after);
  }

  function previousProduct() {
    if (productIndex === 0) return;
    const cursor = productCursors[productIndex - 1] ?? "";
    setProductIndex((index) => index - 1);
    showProductSales(cursor);
  }

  function nextInventory(after: string | null) {
    if (!after || inventoryIndex + 1 >= REPORT_PAGE_CAP) return;
    if (inventoryCursors.includes(after)) {
      setError(tx("This page cursor repeated. Refine the filters."));
      return;
    }
    setInventoryCursors((current) => [
      ...current.slice(0, inventoryIndex + 1),
      after,
    ]);
    setInventoryIndex((index) => index + 1);
    showInventory(after);
  }

  function previousInventory() {
    if (inventoryIndex === 0) return;
    const cursor = inventoryCursors[inventoryIndex - 1] ?? "";
    setInventoryIndex((index) => index - 1);
    showInventory(cursor);
  }

  async function exportCurrent() {
    if (!loaded || !canExport) return;
    setError("");
    try {
      await downloadReportCsv(exportPath(loaded));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The report could not be exported.",
      );
    }
  }

  async function load<T>(path: string, next: (report: T) => LoadedReport) {
    setLoading(true);
    setError("");
    setLoaded(null);
    try {
      setLoaded(next(await operationGet<T>(path)));
    } catch (caught) {
      setLoaded(null);
      setError(
        caught instanceof Error ? caught.message : "Unable to load the report.",
      );
    } finally {
      setLoading(false);
    }
  }

  function generateSelectedReport() {
    if (selectedReport === "daily" && date) {
      void load<PeriodReport>(
        `reports/daily/?date=${encodeURIComponent(date)}`,
        (report) => ({ kind: "daily", report }),
      );
    } else if (selectedReport === "monthly" && year && month) {
      void load<PeriodReport>(
        `reports/monthly/?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`,
        (report) => ({ kind: "monthly", report }),
      );
    } else if (selectedReport === "cash-book" && dateFrom && dateTo) {
      void load<CashBookReport>(
        rangePath("cash-book", dateFrom, dateTo),
        (report) => ({ kind: "cash-book", report }),
      );
    } else if (selectedReport === "profit-loss" && dateFrom && dateTo) {
      void load<ProfitLossReport>(
        rangePath("profit-loss", dateFrom, dateTo),
        (report) => ({ kind: "profit-loss", report }),
      );
    } else if (selectedReport === "product-sales" && dateFrom && dateTo) {
      setProductCursors([""]);
      setProductIndex(0);
      showProductSales();
    } else if (selectedReport === "membership-expiry" && dateFrom && dateTo) {
      void load<MembershipExpiryReport>(
        rangePath("membership-expiry", dateFrom, dateTo),
        (report) => ({ kind: "membership-expiry", report }),
      );
    } else if (selectedReport === "check-ins" && dateFrom && dateTo) {
      void load<CheckInReport>(
        rangePath("check-ins", dateFrom, dateTo),
        (report) => ({ kind: "check-ins", report }),
      );
    } else if (selectedReport === "pt-sessions" && dateFrom && dateTo) {
      void load<PTSessionReport>(
        rangePath("pt-sessions", dateFrom, dateTo),
        (report) => ({ kind: "pt-sessions", report }),
      );
    } else if (selectedReport === "classes" && dateFrom && dateTo) {
      void load<ClassReport>(
        rangePath("classes", dateFrom, dateTo),
        (report) => ({ kind: "classes", report }),
      );
    } else if (selectedReport === "stock-movements" && dateFrom && dateTo) {
      void load<StockMovementReport>(
        rangePath("stock-movements", dateFrom, dateTo),
        (report) => ({ kind: "stock-movements", report }),
      );
    } else if (selectedReport === "inventory") {
      setInventoryCursors([""]);
      setInventoryIndex(0);
      showInventory();
    }
  }

  const rangeRequired = !["daily", "monthly", "inventory"].includes(
    selectedReport,
  );
  const generateDisabled =
    loading ||
    (selectedReport === "daily" && !date) ||
    (selectedReport === "monthly" && (!year || !month)) ||
    (rangeRequired && (!dateFrom || !dateTo));

  return (
    <div className="grid gap-8">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6 [&_.button]:hidden">
        <div className="mb-5">
          <p className="text-xs font-bold tracking-[0.16em] text-amber-600 uppercase">
            {" "}
            {tx("Report builder")}{" "}
          </p>
          <h2 className="mt-1 text-xl font-bold tracking-tight text-slate-900">
            {" "}
            {tx("Choose what you want to review")}{" "}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {" "}
            {tx("Select a date or range, then open the report you need.")}{" "}
          </p>
        </div>
        <div className="mb-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <label className="grid gap-1.5 text-sm font-semibold text-slate-700">
            {" "}
            {tx("Report type")}{" "}
            <select
              className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800"
              onChange={(event) => {
                setSelectedReport(event.target.value as ReportChoice);
                resetPagedReports();
              }}
              value={selectedReport}
            >
              {canViewFinancial ? (
                <optgroup label="Financial">
                  <option value="daily">{tx("Daily report")}</option>
                  <option value="monthly">{tx("Monthly report")}</option>
                  <option value="cash-book">{tx("Cash book")}</option>
                  <option value="profit-loss">{tx("Profit and loss")}</option>
                  <option value="product-sales">{tx("Product sales")}</option>
                </optgroup>
              ) : null}
              <optgroup label="Operations">
                <option value="membership-expiry">
                  {tx("Membership expiry")}
                </option>
                <option value="check-ins">{tx("Check-ins")}</option>
                <option value="pt-sessions">{tx("PT sessions")}</option>
                <option value="classes">{tx("Classes")}</option>
              </optgroup>
              <optgroup label="Inventory">
                <option value="stock-movements">{tx("Stock movements")}</option>
                <option value="inventory">{tx("Inventory")}</option>
              </optgroup>
            </select>
          </label>
          <button
            className="button"
            disabled={generateDisabled}
            onClick={generateSelectedReport}
            type="button"
          >
            {" "}
            {tx("Generate report")}{" "}
          </button>
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          {canViewFinancial ? (
            <>
              <form
                className={
                  selectedReport === "daily"
                    ? "flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 [&_input]:min-h-10 [&_input]:bg-white"
                    : "hidden"
                }
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!date) return;
                  void load<PeriodReport>(
                    `reports/daily/?date=${encodeURIComponent(date)}`,
                    (report) => ({
                      kind: "daily",
                      report,
                    }),
                  );
                }}
              >
                <label className="grid gap-1 text-sm">
                  {" "}
                  {tx("Date")}{" "}
                  <input
                    className="rounded-md border px-3 py-2"
                    onChange={(event) => setDate(event.target.value)}
                    required
                    type="date"
                    value={date}
                  />
                </label>
                <button
                  className="button"
                  disabled={!date || loading}
                  type="submit"
                >
                  {" "}
                  {tx("Show daily report")}{" "}
                </button>
              </form>
              <form
                className={
                  selectedReport === "monthly"
                    ? "flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 [&_input]:min-h-10 [&_input]:bg-white"
                    : "hidden"
                }
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!year || !month) return;
                  void load<PeriodReport>(
                    `reports/monthly/?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`,
                    (report) => ({ kind: "monthly", report }),
                  );
                }}
              >
                <label className="hidden">
                  {" "}
                  {tx("Year")}{" "}
                  <DigitInput
                    className="w-28 rounded-md border px-3 py-2"
                    mode={{ kind: "integer" }}
                    onChange={(event) => setYear(event.target.value)}
                    required
                    value={year}
                  />
                </label>
                <label className="hidden">
                  {" "}
                  {tx("Month")}{" "}
                  <DigitInput
                    className="w-24 rounded-md border px-3 py-2"
                    mode={{ kind: "integer" }}
                    onChange={(event) => setMonth(event.target.value)}
                    required
                    value={month}
                  />
                </label>
                <label className="grid w-full gap-1.5 text-sm font-semibold text-slate-700 sm:max-w-xs">
                  {" "}
                  {tx("Reporting month")}{" "}
                  <input
                    className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800"
                    onChange={(event) => {
                      const [nextYear = "", nextMonth = ""] =
                        event.target.value.split("-");
                      setYear(nextYear);
                      setMonth(nextMonth);
                    }}
                    type="month"
                    value={
                      year && month ? `${year}-${month.padStart(2, "0")}` : ""
                    }
                  />
                </label>
                <button
                  className="button"
                  disabled={!year || !month || loading}
                  type="submit"
                >
                  {" "}
                  {tx("Show monthly report")}{" "}
                </button>
              </form>
            </>
          ) : null}
          <form
            className={
              rangeRequired
                ? "flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 xl:col-span-2 [&_input]:min-h-10 [&_input]:bg-white"
                : "hidden"
            }
          >
            <label className="grid gap-1 text-sm">
              {" "}
              {tx("From date")}{" "}
              <input
                className="rounded-md border px-3 py-2"
                onChange={(event) => {
                  setDateFrom(event.target.value);
                  resetPagedReports();
                }}
                required
                type="date"
                value={dateFrom}
              />
            </label>
            <label className="grid gap-1 text-sm">
              {" "}
              {tx("To date")}{" "}
              <input
                className="rounded-md border px-3 py-2"
                onChange={(event) => {
                  setDateTo(event.target.value);
                  resetPagedReports();
                }}
                required
                type="date"
                value={dateTo}
              />
            </label>
            <div className="hidden">
              <div className="grid gap-5 lg:grid-cols-3">
                {canViewFinancial ? (
                  <div>
                    <p className="mb-2 text-xs font-bold tracking-wide text-slate-500 uppercase">
                      {" "}
                      {tx("Financial")}{" "}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        className="button"
                        disabled={!dateFrom || !dateTo || loading}
                        onClick={() => {
                          void load<CashBookReport>(
                            rangePath("cash-book", dateFrom, dateTo),
                            (report) => ({
                              kind: "cash-book",
                              report,
                            }),
                          );
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Show cash book")}{" "}
                      </button>
                      <button
                        className="button"
                        disabled={!dateFrom || !dateTo || loading}
                        onClick={() => {
                          void load<ProfitLossReport>(
                            rangePath("profit-loss", dateFrom, dateTo),
                            (report) => ({
                              kind: "profit-loss",
                              report,
                            }),
                          );
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Show profit and loss")}{" "}
                      </button>
                      <button
                        className="button"
                        disabled={!dateFrom || !dateTo || loading}
                        onClick={() => {
                          setProductCursors([""]);
                          setProductIndex(0);
                          showProductSales();
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Show product sales")}{" "}
                      </button>
                    </div>
                  </div>
                ) : null}
                <div>
                  <p className="mb-2 text-xs font-bold tracking-wide text-slate-500 uppercase">
                    {" "}
                    {tx("Operations")}{" "}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      className="button"
                      disabled={!dateFrom || !dateTo || loading}
                      onClick={() => {
                        void load<MembershipExpiryReport>(
                          rangePath("membership-expiry", dateFrom, dateTo),
                          (report) => ({
                            kind: "membership-expiry",
                            report,
                          }),
                        );
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show membership expiry")}{" "}
                    </button>
                    <button
                      className="button"
                      disabled={!dateFrom || !dateTo || loading}
                      onClick={() => {
                        void load<CheckInReport>(
                          rangePath("check-ins", dateFrom, dateTo),
                          (report) => ({
                            kind: "check-ins",
                            report,
                          }),
                        );
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show check-ins")}{" "}
                    </button>
                    <button
                      className="button"
                      disabled={!dateFrom || !dateTo || loading}
                      onClick={() => {
                        void load<PTSessionReport>(
                          rangePath("pt-sessions", dateFrom, dateTo),
                          (report) => ({
                            kind: "pt-sessions",
                            report,
                          }),
                        );
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show PT sessions")}{" "}
                    </button>
                    <button
                      className="button"
                      disabled={!dateFrom || !dateTo || loading}
                      onClick={() => {
                        void load<ClassReport>(
                          rangePath("classes", dateFrom, dateTo),
                          (report) => ({
                            kind: "classes",
                            report,
                          }),
                        );
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show classes")}{" "}
                    </button>
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-xs font-bold tracking-wide text-slate-500 uppercase">
                    {" "}
                    {tx("Inventory")}{" "}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      className="button"
                      disabled={!dateFrom || !dateTo || loading}
                      onClick={() => {
                        void load<StockMovementReport>(
                          rangePath("stock-movements", dateFrom, dateTo),
                          (report) => ({
                            kind: "stock-movements",
                            report,
                          }),
                        );
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show stock movements")}{" "}
                    </button>
                    <button
                      className="button"
                      disabled={loading}
                      onClick={() => {
                        setInventoryCursors([""]);
                        setInventoryIndex(0);
                        showInventory();
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Show inventory")}{" "}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </form>
        </div>
      </section>
      {error ? (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 p-5 text-sm font-medium text-red-800"
          role="alert"
        >
          {tx(error)}
        </div>
      ) : null}
      {loading ? (
        <div className="grid gap-4" role="status">
          <span className="sr-only">{tx("Loading report…")}</span>
          <div className="h-28 animate-pulse rounded-2xl bg-slate-200" />
          <div className="h-72 animate-pulse rounded-2xl bg-slate-200" />
        </div>
      ) : null}
      {!loading && !error && !loaded ? (
        <div
          className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center"
          role="status"
        >
          <span
            className="mx-auto grid size-12 place-items-center rounded-full bg-slate-200 text-xl text-slate-500"
            aria-hidden="true"
          >
            ↗
          </span>
          <p className="mt-4 font-semibold text-slate-700">
            {canViewFinancial
              ? tx("Choose a date, month, or range.")
              : tx("Choose a range.")}
          </p>
          <p className="mt-1 text-sm text-slate-500">
            {" "}
            {tx("Your selected report will appear here.")}{" "}
          </p>
        </div>
      ) : null}
      {loaded && canExport && !loading ? (
        <div className="flex justify-end">
          <button
            className="button"
            onClick={() => void exportCurrent()}
            type="button"
          >
            {" "}
            {tx("Export CSV")}{" "}
          </button>
        </div>
      ) : null}
      <div className="[&>section]:rounded-2xl [&>section]:border [&>section]:border-slate-200 [&>section]:bg-white [&>section]:p-5 [&>section]:shadow-sm [&>section]:sm:p-6 [&>section>h2]:text-2xl [&>section>h2]:font-black [&>section>h2]:tracking-tight [&>section>h2]:text-slate-900 [&>section>p]:text-sm [&>section>p]:text-slate-500">
        {loaded?.kind === "daily" || loaded?.kind === "monthly" ? (
          <ReportView kind={loaded.kind} report={loaded.report} />
        ) : null}
        {loaded?.kind === "cash-book" ? (
          <CashBookView report={loaded.report} />
        ) : null}
        {loaded?.kind === "profit-loss" ? (
          <ProfitLossView report={loaded.report} />
        ) : null}
        {loaded?.kind === "membership-expiry" ? (
          <MembershipExpiryView report={loaded.report} />
        ) : null}
        {loaded?.kind === "check-ins" ? (
          <CheckInReportView report={loaded.report} />
        ) : null}
        {loaded?.kind === "pt-sessions" ? (
          <PTSessionReportView report={loaded.report} />
        ) : null}
        {loaded?.kind === "classes" ? (
          <ClassReportView report={loaded.report} />
        ) : null}
        {loaded?.kind === "product-sales" ? (
          <ProductSalesView
            bounded={
              loaded.report.meta.has_more && productIndex + 1 >= REPORT_PAGE_CAP
            }
            hasMore={
              loaded.report.meta.has_more && productIndex + 1 < REPORT_PAGE_CAP
            }
            index={productIndex}
            loading={loading}
            onNext={() => nextProduct(loaded.report.meta.next_after)}
            onPrevious={previousProduct}
            report={loaded.report}
          />
        ) : null}
        {loaded?.kind === "stock-movements" ? (
          <StockMovementView report={loaded.report} />
        ) : null}
        {loaded?.kind === "inventory" ? (
          <InventoryView
            bounded={
              loaded.report.meta.has_more &&
              inventoryIndex + 1 >= REPORT_PAGE_CAP
            }
            hasMore={
              loaded.report.meta.has_more &&
              inventoryIndex + 1 < REPORT_PAGE_CAP
            }
            index={inventoryIndex}
            loading={loading}
            onNext={() => nextInventory(loaded.report.meta.next_after)}
            onPrevious={previousInventory}
            report={loaded.report}
          />
        ) : null}
      </div>
    </div>
  );
}

function exportPath(loaded: LoadedReport): string {
  const params = new URLSearchParams();
  if (loaded.kind === "daily") {
    params.set("date", loaded.report.range.date_from);
  } else if (loaded.kind === "monthly") {
    const [year, month] = loaded.report.range.date_from.split("-");
    params.set("year", year ?? "");
    params.set("month", String(Number(month)));
  } else if (loaded.kind !== "inventory") {
    params.set("date_from", loaded.report.range.date_from);
    params.set("date_to", loaded.report.range.date_to);
  }
  const query = params.toString();
  return `/api/operations/reports/${loaded.kind}/export/${query ? `?${query}` : ""}`;
}

function rangePath(
  kind:
    | "cash-book"
    | "profit-loss"
    | "membership-expiry"
    | "check-ins"
    | "pt-sessions"
    | "classes"
    | "stock-movements",
  dateFrom: string,
  dateTo: string,
) {
  const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  return `reports/${kind}/?${query.toString()}`;
}

function ReportView({
  kind,
  report,
}: {
  kind: "daily" | "monthly";
  report: PeriodReport;
}) {
  const tx = useTx();
  const title = kind === "daily" ? "Daily report" : "Monthly report";
  return (
    <section aria-labelledby="period-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="period-report-title">
        {tx(title)}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails
        entries={[
          ["Revenue", report.source_status_policy.revenue],
          ["Expenses", report.source_status_policy.expenses],
          ["Operations", report.source_status_policy.operations],
        ]}
      />
      <RowsTable columns={COLUMNS} rows={report.rows} />
      <section
        aria-labelledby="report-totals-title"
        className="rounded-xl bg-slate-50 p-4"
      >
        <h3
          className="text-base font-bold text-slate-900"
          id="report-totals-title"
        >
          {" "}
          {tx("Totals")}{" "}
        </h3>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {TOTAL_FIELDS.map((field) => (
            <div className="rounded-lg bg-white p-3" key={field.key}>
              <dt className="text-xs font-medium text-slate-500">
                {tx(field.label)}
              </dt>
              <dd className="mt-1 font-bold text-slate-900">
                {report.totals[field.key]}
              </dd>
            </div>
          ))}
        </dl>
        <ul className="mt-4 flex flex-wrap gap-2">
          {(report.totals.expenses_by_category ?? []).map((category) => (
            <li
              className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600"
              key={category.system_key}
            >
              {category.system_key} {category.amount}
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}

function CashBookView({ report }: { report: CashBookReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="cash-book-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="cash-book-title">
        {" "}
        {tx("Cash book")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails
        entries={[
          ["Sessions", report.source_status_policy.sessions],
          ["Transactions", report.source_status_policy.transactions],
        ]}
      />
      <RowsTable columns={CASH_COLUMNS} rows={report.rows} />
      <TotalsList fields={CASH_TOTALS} totals={report.totals} />
    </section>
  );
}

function ProfitLossView({ report }: { report: ProfitLossReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="profit-loss-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="profit-loss-title">
        {" "}
        {tx("Profit and loss")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails
        entries={[
          ["Revenue", report.source_status_policy.revenue],
          ["Expenses", report.source_status_policy.expenses],
          ["Operations", report.source_status_policy.operations],
        ]}
      />
      <RowsTable columns={PROFIT_COLUMNS} rows={report.rows} />
      <section
        aria-labelledby="report-totals-title"
        className="rounded-xl bg-slate-50 p-4"
      >
        <h3
          className="text-base font-bold text-slate-900"
          id="report-totals-title"
        >
          {" "}
          {tx("Totals")}{" "}
        </h3>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {PROFIT_TOTALS.map((field) => (
            <div className="rounded-lg bg-white p-3" key={field.key}>
              <dt className="text-xs font-medium text-slate-500">
                {tx(field.label)}
              </dt>
              <dd className="mt-1 font-bold text-slate-900">
                {report.totals[field.key]}
              </dd>
            </div>
          ))}
        </dl>
        <ul className="mt-4 flex flex-wrap gap-2">
          {(report.totals.expenses_by_category ?? []).map((category) => (
            <li
              className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600"
              key={category.system_key}
            >
              {category.system_key} {category.amount}
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}

const EXPIRY_COLUMNS: {
  key: keyof MembershipExpiryReport["rows"][number];
  label: string;
}[] = [
  { key: "date", label: "Date" },
  { key: "expiring_memberships", label: "Expiring memberships" },
  { key: "unique_members", label: "Unique members" },
  { key: "active_memberships", label: "Active memberships" },
  { key: "suspended_memberships", label: "Suspended memberships" },
  { key: "expired_memberships", label: "Expired memberships" },
];

const CHECK_IN_COLUMNS: {
  key: keyof CheckInReport["rows"][number];
  label: string;
}[] = [
  { key: "date", label: "Date" },
  { key: "check_ins", label: "Check-ins" },
  { key: "unique_attendees", label: "Unique attendees" },
  { key: "manual_check_ins", label: "Manual check-ins" },
  { key: "qr_check_ins", label: "QR check-ins" },
  { key: "nfc_check_ins", label: "NFC check-ins" },
  { key: "biometric_check_ins", label: "Biometric check-ins" },
  { key: "check_outs", label: "Check-outs" },
  { key: "open_visits_from_day", label: "Open visits from day" },
];

function MembershipExpiryView({ report }: { report: MembershipExpiryReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="membership-expiry-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="membership-expiry-title">
        {" "}
        {tx("Membership expiry")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <RowsTable columns={EXPIRY_COLUMNS} rows={report.rows} />
      <CountTotals fields={EXPIRY_COLUMNS.slice(1)} totals={report.totals} />
    </section>
  );
}

const PT_SESSION_COLUMNS: {
  key: keyof PTSessionReport["rows"][number];
  label: string;
}[] = [
  { key: "date", label: "Date" },
  { key: "bookings", label: "Bookings" },
  { key: "unique_members", label: "Unique members" },
  { key: "unique_trainers", label: "Unique trainers" },
  { key: "booked_sessions", label: "Booked sessions" },
  { key: "attended_sessions", label: "Attended sessions" },
  { key: "no_show_sessions", label: "No-show sessions" },
  { key: "cancelled_sessions", label: "Cancelled sessions" },
  { key: "metered_sessions", label: "Metered sessions" },
  { key: "unmetered_sessions", label: "Unmetered sessions" },
];

const CLASS_COLUMNS: {
  key: keyof ClassReport["rows"][number];
  label: string;
}[] = [
  { key: "date", label: "Date" },
  { key: "occurrences", label: "Occurrences" },
  { key: "scheduled_occurrences", label: "Scheduled occurrences" },
  { key: "completed_occurrences", label: "Completed occurrences" },
  { key: "cancelled_occurrences", label: "Cancelled occurrences" },
  { key: "available_capacity", label: "Available capacity" },
  { key: "bookings", label: "Bookings" },
  { key: "unique_members", label: "Unique members" },
  { key: "booked_slots", label: "Booked slots" },
  { key: "attended_slots", label: "Attended slots" },
  { key: "no_show_slots", label: "No-show slots" },
  { key: "cancelled_slots", label: "Cancelled slots" },
  { key: "reserved_slots", label: "Reserved slots" },
  { key: "remaining_capacity", label: "Remaining capacity" },
];

function CheckInReportView({ report }: { report: CheckInReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="check-in-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="check-in-report-title">
        {" "}
        {tx("Check-ins")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <RowsTable columns={CHECK_IN_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={[
          ...CHECK_IN_COLUMNS.slice(1),
          { key: "currently_checked_in", label: tx("Currently checked in") },
        ]}
        totals={report.totals}
      />
    </section>
  );
}

function PTSessionReportView({ report }: { report: PTSessionReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="pt-session-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="pt-session-report-title">
        {" "}
        {tx("PT sessions")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <RowsTable columns={PT_SESSION_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={PT_SESSION_COLUMNS.slice(1)}
        totals={report.totals}
      />
    </section>
  );
}

function ClassReportView({ report }: { report: ClassReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="class-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="class-report-title">
        {" "}
        {tx("Classes")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <RowsTable columns={CLASS_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={[
          ...CLASS_COLUMNS.slice(1),
          { key: "unique_trainers", label: tx("Unique trainers") },
        ]}
        totals={report.totals}
      />
    </section>
  );
}

const PRODUCT_SALES_COLUMNS: {
  key: keyof ProductSalesReport["rows"][number];
  label: string;
}[] = [
  { key: "sku", label: "SKU" },
  { key: "name", label: "Name" },
  { key: "is_active", label: "Active" },
  { key: "quantity_sold", label: "Quantity sold" },
  { key: "revenue", label: "Revenue" },
  { key: "cogs", label: "COGS" },
  { key: "gross_profit", label: "Gross profit" },
];

const STOCK_COLUMNS: {
  key: keyof StockMovementReport["rows"][number];
  label: string;
}[] = [
  { key: "date", label: "Date" },
  { key: "opening_quantity", label: "Opening" },
  { key: "purchase_quantity", label: "Purchase" },
  { key: "sale_quantity", label: "Sale" },
  { key: "sale_return_quantity", label: "Sale return" },
  { key: "damage_quantity", label: "Damage" },
  { key: "loss_quantity", label: "Loss" },
  { key: "adjustment_quantity", label: "Adjustment" },
  { key: "correction_quantity", label: "Correction" },
  { key: "net_quantity", label: "Net" },
];

const INVENTORY_COLUMNS: {
  key: keyof InventoryReport["rows"][number];
  label: string;
}[] = [
  { key: "sku", label: "SKU" },
  { key: "name", label: "Name" },
  { key: "category_code", label: "Category code" },
  { key: "category_name", label: "Category" },
  { key: "unit", label: "Unit" },
  { key: "is_active", label: "Active" },
  { key: "stock_quantity", label: "Stock" },
  { key: "low_stock_threshold", label: "Low stock threshold" },
  { key: "is_low_stock", label: "Low stock" },
];

function ProductSalesView({
  report,
  index,
  hasMore,
  bounded,
  loading,
  onPrevious,
  onNext,
}: {
  report: ProductSalesReport;
  index: number;
  hasMore: boolean;
  bounded: boolean;
  loading: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const tx = useTx();
  return (
    <section aria-labelledby="product-sales-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="product-sales-title">
        {" "}
        {tx("Product sales")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <CatalogTable columns={PRODUCT_SALES_COLUMNS} rows={report.rows} />
      <TotalsList
        fields={[
          { key: "quantity_sold", label: tx("Quantity sold") },
          { key: "revenue", label: tx("Revenue") },
          { key: "cogs", label: "COGS" },
          { key: "gross_profit", label: tx("Gross profit") },
          { key: "product_count", label: tx("Products") },
          { key: "active_product_count", label: tx("Active products") },
        ]}
        totals={report.totals}
      />
      <CursorControls
        bounded={bounded}
        hasMore={hasMore}
        index={index}
        onNext={onNext}
        onPrevious={onPrevious}
        pending={loading}
      />
    </section>
  );
}

function StockMovementView({ report }: { report: StockMovementReport }) {
  const tx = useTx();
  return (
    <section aria-labelledby="stock-movement-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="stock-movement-title">
        {" "}
        {tx("Stock movements")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <RowsTable columns={STOCK_COLUMNS} rows={report.rows} />
      <TotalsList
        fields={STOCK_COLUMNS.slice(1).map((column) => ({
          key: column.key as keyof StockMovementReport["totals"],
          label: column.label,
        }))}
        totals={report.totals}
      />
    </section>
  );
}

function InventoryView({
  report,
  index,
  hasMore,
  bounded,
  loading,
  onPrevious,
  onNext,
}: {
  report: InventoryReport;
  index: number;
  hasMore: boolean;
  bounded: boolean;
  loading: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const tx = useTx();
  return (
    <section aria-labelledby="inventory-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="inventory-report-title">
        {" "}
        {tx("Inventory")}{" "}
      </h2>
      <ReportIdentity report={report} />
      <PolicyDetails entries={[["Source", report.source_status_policy]]} />
      <CatalogTable columns={INVENTORY_COLUMNS} rows={report.rows} />
      <TotalsList
        fields={[
          { key: "product_count", label: tx("Products") },
          { key: "active_product_count", label: tx("Active products") },
          { key: "low_stock_product_count", label: tx("Low stock products") },
          { key: "total_stock_quantity", label: tx("Total stock") },
        ]}
        totals={report.totals}
      />
      <CursorControls
        bounded={bounded}
        hasMore={hasMore}
        index={index}
        onNext={onNext}
        onPrevious={onPrevious}
        pending={loading}
      />
    </section>
  );
}

function CountTotals({
  fields,
  totals,
}: {
  fields: readonly { key: string; label: string }[];
  totals: object;
}) {
  const tx = useTx();
  return (
    <section
      aria-labelledby="report-totals-title"
      className="rounded-xl bg-slate-50 p-4"
    >
      <h3
        className="text-base font-bold text-slate-900"
        id="report-totals-title"
      >
        {" "}
        {tx("Totals")}{" "}
      </h3>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {fields.map((field) => (
          <div className="rounded-lg bg-white p-3" key={field.key}>
            <dt className="text-xs font-medium text-slate-500">
              {tx(field.label)}
            </dt>
            <dd className="mt-1 text-lg font-bold text-slate-900">
              {countValue(totals, field.key)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function countValue(totals: object, key: string): number {
  const value = (totals as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

function ReportIdentity({ report }: { report: Identity }) {
  const tx = useTx();
  const generatedLabel = formatAppDateTime(report.generated_at);
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600">
      <span className="inline-flex items-center gap-2">
        <span
          className="size-2 rounded-full bg-emerald-500"
          aria-hidden="true"
        />
        <strong className="text-sm text-slate-900">{report.gym.name}</strong>
      </span>
      <span className="rounded bg-white px-2 py-1 font-semibold">
        {report.gym.code}
      </span>
      {report.range ? (
        <span>
          {formatAppDate(report.range.date_from)} {tx("to")}{" "}
          {formatAppDate(report.range.date_to)}
        </span>
      ) : null}
      <span>{report.timezone}</span>
      {report.currency ? <span>{tx(report.currency)}</span> : null}
      <span className="ml-auto text-slate-400">
        {tx("Generated")} {generatedLabel}
      </span>
    </div>
  );
}

function PolicyDetails({ entries }: { entries: [string, string][] }) {
  const tx = useTx();
  return (
    <details className="rounded-xl border border-slate-200 bg-white">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-slate-600 marker:hidden">
        <span className="inline-flex items-center gap-2">
          <span
            className="grid size-5 place-items-center rounded-full bg-emerald-100 text-xs text-emerald-700"
            aria-hidden="true"
          >
            ✓
          </span>{" "}
          {tx("Verified data policy")}{" "}
        </span>
      </summary>
      <dl className="grid gap-3 border-t border-slate-100 bg-slate-50 p-4">
        {entries.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs font-bold text-slate-500">{tx(label)}</dt>
            <dd className="mt-1 break-words text-xs leading-5 text-slate-600">
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function CatalogTable<Row extends { product_id: string; sku: string }>({
  columns,
  rows,
}: {
  columns: { key: keyof Row & string; label: string }[];
  rows: Row[];
}) {
  const tx = useTx();
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200">
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <caption className="sr-only">{tx("Report rows")}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  className="whitespace-nowrap px-4 py-3 text-xs font-semibold"
                  key={column.key}
                  scope="col"
                >
                  {tx(column.label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {rows.map((row) => (
              <tr className="hover:bg-slate-50" key={row.product_id}>
                <th
                  className="whitespace-nowrap px-4 py-3 font-semibold text-slate-900"
                  scope="row"
                >
                  <IdentifierValue value={row.sku} />
                </th>
                {columns.slice(1).map((column) => (
                  <td
                    className="whitespace-nowrap px-4 py-3 text-slate-600"
                    key={column.key}
                  >
                    <IdentifierValue
                      value={catalogCell(column.key, row[column.key])}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function catalogCell(key: string, value: unknown): string {
  if (key === "is_active") return value ? "Active" : "Archived";
  if (key === "is_low_stock") return value ? "Yes" : "No";
  return String(value);
}

function RowsTable<Row extends { date: string }>({
  columns,
  rows,
}: {
  columns: { key: keyof Row & string; label: string }[];
  rows: Row[];
}) {
  const tx = useTx();
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200">
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <caption className="sr-only">{tx("Report rows")}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  className="whitespace-nowrap px-4 py-3 text-xs font-semibold"
                  key={column.key}
                  scope="col"
                >
                  {tx(column.label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {rows.map((row) => (
              <tr className="hover:bg-slate-50" key={row.date}>
                <th
                  className="whitespace-nowrap px-4 py-3 font-semibold text-slate-900"
                  scope="row"
                >
                  <IdentifierValue value={formatAppDate(row.date)} />
                </th>
                {columns.slice(1).map((column) => (
                  <td
                    className="whitespace-nowrap px-4 py-3 text-slate-600"
                    key={column.key}
                  >
                    <IdentifierValue value={String(row[column.key])} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TotalsList<Key extends string>({
  fields,
  totals,
}: {
  fields: { key: Key; label: string }[];
  totals: Record<Key, string | number>;
}) {
  const tx = useTx();
  return (
    <section
      aria-labelledby="report-totals-title"
      className="rounded-xl bg-slate-50 p-4"
    >
      <h3
        className="text-base font-bold text-slate-900"
        id="report-totals-title"
      >
        {" "}
        {tx("Totals")}{" "}
      </h3>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {fields.map((field) => (
          <div className="rounded-lg bg-white p-3" key={field.key}>
            <dt className="text-xs font-medium text-slate-500">
              {tx(field.label)}
            </dt>
            <dd className="mt-1 font-bold text-slate-900">
              {totals[field.key]}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

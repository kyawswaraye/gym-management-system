"use client";

import { useState } from "react";

import { CursorControls } from "@/features/finance/ui";
import { operationGet } from "@/features/operations/client";
import { downloadReportCsv } from "@/features/reports/download";
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
  const [date, setDate] = useState("");
  const [year, setYear] = useState("");
  const [month, setMonth] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
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
      setError("This page cursor repeated. Refine the filters.");
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
      setError("This page cursor repeated. Refine the filters.");
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

  return (
    <div className="grid gap-6">
      {canViewFinancial ? (
        <>
          <form
            className="flex flex-wrap items-end gap-3"
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
              Date
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
              Show daily report
            </button>
          </form>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!year || !month) return;
              void load<PeriodReport>(
                `reports/monthly/?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`,
                (report) => ({ kind: "monthly", report }),
              );
            }}
          >
            <label className="grid gap-1 text-sm">
              Year
              <input
                className="w-28 rounded-md border px-3 py-2"
                inputMode="numeric"
                onChange={(event) => setYear(event.target.value)}
                required
                value={year}
              />
            </label>
            <label className="grid gap-1 text-sm">
              Month
              <input
                className="w-24 rounded-md border px-3 py-2"
                inputMode="numeric"
                onChange={(event) => setMonth(event.target.value)}
                required
                value={month}
              />
            </label>
            <button
              className="button"
              disabled={!year || !month || loading}
              type="submit"
            >
              Show monthly report
            </button>
          </form>
        </>
      ) : null}
      <form className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-sm">
          From date
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
          To date
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
        {canViewFinancial ? (
          <>
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
              Show cash book
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
              Show profit and loss
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
              Show product sales
            </button>
          </>
        ) : null}
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
          Show membership expiry
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
          Show check-ins
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
          Show PT sessions
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
          Show classes
        </button>
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
          Show stock movements
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
          Show inventory
        </button>
      </form>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="muted" role="status">
          Loading report…
        </p>
      ) : null}
      {!loading && !error && !loaded ? (
        <p className="muted" role="status">
          {canViewFinancial
            ? "Choose a date, month, or range."
            : "Choose a range."}
        </p>
      ) : null}
      {loaded && canExport && !loading ? (
        <button
          className="button"
          onClick={() => void exportCurrent()}
          type="button"
        >
          Export CSV
        </button>
      ) : null}
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
            loaded.report.meta.has_more && inventoryIndex + 1 >= REPORT_PAGE_CAP
          }
          hasMore={
            loaded.report.meta.has_more && inventoryIndex + 1 < REPORT_PAGE_CAP
          }
          index={inventoryIndex}
          loading={loading}
          onNext={() => nextInventory(loaded.report.meta.next_after)}
          onPrevious={previousInventory}
          report={loaded.report}
        />
      ) : null}
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
  const title = kind === "daily" ? "Daily report" : "Monthly report";
  return (
    <section aria-labelledby="period-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="period-report-title">
        {title}
      </h2>
      <ReportIdentity report={report} />
      <dl className="grid gap-2">
        <div>
          <dt>Revenue status</dt>
          <dd>{report.source_status_policy.revenue}</dd>
        </div>
        <div>
          <dt>Expense status</dt>
          <dd>{report.source_status_policy.expenses}</dd>
        </div>
        <div>
          <dt>Operations status</dt>
          <dd>{report.source_status_policy.operations}</dd>
        </div>
      </dl>
      <RowsTable columns={COLUMNS} rows={report.rows} />
      <section aria-labelledby="report-totals-title">
        <h3 className="text-lg font-semibold" id="report-totals-title">
          Totals
        </h3>
        <dl className="mt-3 grid gap-2">
          {TOTAL_FIELDS.map((field) => (
            <div key={field.key}>
              <dt>{field.label}</dt>
              <dd>{report.totals[field.key]}</dd>
            </div>
          ))}
        </dl>
        <ul className="mt-3 grid gap-1">
          {report.totals.expenses_by_category.map((category) => (
            <li key={category.system_key}>
              {category.system_key} {category.amount}
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}

function CashBookView({ report }: { report: CashBookReport }) {
  return (
    <section aria-labelledby="cash-book-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="cash-book-title">
        Cash book
      </h2>
      <ReportIdentity report={report} />
      <dl className="grid gap-2">
        <div>
          <dt>Session status</dt>
          <dd>{report.source_status_policy.sessions}</dd>
        </div>
        <div>
          <dt>Transaction status</dt>
          <dd>{report.source_status_policy.transactions}</dd>
        </div>
      </dl>
      <RowsTable columns={CASH_COLUMNS} rows={report.rows} />
      <TotalsList fields={CASH_TOTALS} totals={report.totals} />
    </section>
  );
}

function ProfitLossView({ report }: { report: ProfitLossReport }) {
  return (
    <section aria-labelledby="profit-loss-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="profit-loss-title">
        Profit and loss
      </h2>
      <ReportIdentity report={report} />
      <dl className="grid gap-2">
        <div>
          <dt>Revenue status</dt>
          <dd>{report.source_status_policy.revenue}</dd>
        </div>
        <div>
          <dt>Expense status</dt>
          <dd>{report.source_status_policy.expenses}</dd>
        </div>
        <div>
          <dt>Operations status</dt>
          <dd>{report.source_status_policy.operations}</dd>
        </div>
      </dl>
      <RowsTable columns={PROFIT_COLUMNS} rows={report.rows} />
      <section aria-labelledby="report-totals-title">
        <h3 className="text-lg font-semibold" id="report-totals-title">
          Totals
        </h3>
        <dl className="mt-3 grid gap-2">
          {PROFIT_TOTALS.map((field) => (
            <div key={field.key}>
              <dt>{field.label}</dt>
              <dd>{report.totals[field.key]}</dd>
            </div>
          ))}
        </dl>
        <ul className="mt-3 grid gap-1">
          {report.totals.expenses_by_category.map((category) => (
            <li key={category.system_key}>
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
  return (
    <section aria-labelledby="membership-expiry-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="membership-expiry-title">
        Membership expiry
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
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
  return (
    <section aria-labelledby="check-in-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="check-in-report-title">
        Check-ins
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      <RowsTable columns={CHECK_IN_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={[
          ...CHECK_IN_COLUMNS.slice(1),
          { key: "currently_checked_in", label: "Currently checked in" },
        ]}
        totals={report.totals}
      />
    </section>
  );
}

function PTSessionReportView({ report }: { report: PTSessionReport }) {
  return (
    <section aria-labelledby="pt-session-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="pt-session-report-title">
        PT sessions
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      <RowsTable columns={PT_SESSION_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={PT_SESSION_COLUMNS.slice(1)}
        totals={report.totals}
      />
    </section>
  );
}

function ClassReportView({ report }: { report: ClassReport }) {
  return (
    <section aria-labelledby="class-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="class-report-title">
        Classes
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      <RowsTable columns={CLASS_COLUMNS} rows={report.rows} />
      <CountTotals
        fields={[
          ...CLASS_COLUMNS.slice(1),
          { key: "unique_trainers", label: "Unique trainers" },
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
  return (
    <section aria-labelledby="product-sales-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="product-sales-title">
        Product sales
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      <CatalogTable columns={PRODUCT_SALES_COLUMNS} rows={report.rows} />
      <TotalsList
        fields={[
          { key: "quantity_sold", label: "Quantity sold" },
          { key: "revenue", label: "Revenue" },
          { key: "cogs", label: "COGS" },
          { key: "gross_profit", label: "Gross profit" },
          { key: "product_count", label: "Products" },
          { key: "active_product_count", label: "Active products" },
        ]}
        totals={report.totals}
      />
      <p>More results: {report.meta.has_more ? "yes" : "no"}</p>
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
  return (
    <section aria-labelledby="stock-movement-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="stock-movement-title">
        Stock movements
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
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
  return (
    <section aria-labelledby="inventory-report-title" className="grid gap-4">
      <h2 className="text-xl font-semibold" id="inventory-report-title">
        Inventory
      </h2>
      <ReportIdentity report={report} />
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      <CatalogTable columns={INVENTORY_COLUMNS} rows={report.rows} />
      <TotalsList
        fields={[
          { key: "product_count", label: "Products" },
          { key: "active_product_count", label: "Active products" },
          { key: "low_stock_product_count", label: "Low stock products" },
          { key: "total_stock_quantity", label: "Total stock" },
        ]}
        totals={report.totals}
      />
      <p>More results: {report.meta.has_more ? "yes" : "no"}</p>
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
  return (
    <section aria-labelledby="report-totals-title">
      <h3 className="text-lg font-semibold" id="report-totals-title">
        Totals
      </h3>
      <dl className="mt-3 grid gap-2">
        {fields.map((field) => (
          <div key={field.key}>
            <dt>{field.label}</dt>
            <dd>{countValue(totals, field.key)}</dd>
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
  return (
    <>
      <p>{report.gym.name}</p>
      <p>{report.gym.code}</p>
      {report.range ? (
        <p>
          {report.range.date_from} to {report.range.date_to}
        </p>
      ) : null}
      <p>{report.timezone}</p>
      {report.currency ? <p>{report.currency}</p> : null}
      <p>{report.generated_at}</p>
    </>
  );
}

function CatalogTable<Row extends { product_id: string; sku: string }>({
  columns,
  rows,
}: {
  columns: { key: keyof Row & string; label: string }[];
  rows: Row[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Report rows</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th className="px-2 py-1" key={column.key} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.product_id}>
              <th className="px-2 py-1" scope="row">
                {row.sku}
              </th>
              {columns.slice(1).map((column) => (
                <td className="px-2 py-1" key={column.key}>
                  {catalogCell(column.key, row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
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
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Report rows</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th className="px-2 py-1" key={column.key} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date}>
              <th className="px-2 py-1" scope="row">
                {row.date}
              </th>
              {columns.slice(1).map((column) => (
                <td className="px-2 py-1" key={column.key}>
                  {String(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
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
  return (
    <section aria-labelledby="report-totals-title">
      <h3 className="text-lg font-semibold" id="report-totals-title">
        Totals
      </h3>
      <dl className="mt-3 grid gap-2">
        {fields.map((field) => (
          <div key={field.key}>
            <dt>{field.label}</dt>
            <dd>{totals[field.key]}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

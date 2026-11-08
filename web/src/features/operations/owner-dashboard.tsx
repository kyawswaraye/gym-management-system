"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import {
  ArcGauge,
  ShareDonut,
  Sparkline,
  TrendChart,
} from "@/features/operations/dashboard-charts";
import { operationGet } from "@/features/operations/client";
import {
  formatMoneyCents,
  isMonthEnd,
  monthKey,
  moneyCents,
  monthsCovering,
  previousMonth,
  rowsOf,
  shouldBucketWeekly,
  toChartPoints,
} from "@/features/operations/dashboard-series";
import type { ChartPoint } from "@/features/operations/dashboard-series";
import { LoadingMark } from "@/components/loading-mark";
import { useTx } from "@/i18n/use-tx";
import type { DashboardData, DashboardRangePreset } from "@/types/api";

const PRESETS: { value: DashboardRangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "this_week", label: "This week" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "this_year", label: "This year" },
];

const SOURCE_LABELS: Record<string, string> = {
  NEW_MEMBER: "New member",
  RENEWAL: "Member renewal",
  PT: "PT",
  CLASS: "Class",
  PRODUCT: "Product sales",
  OTHER: "Other",
};

const SOURCE_COLORS: Record<string, string> = {
  NEW_MEMBER: "var(--color-chart-indigo)",
  RENEWAL: "var(--color-chart-teal)",
  PT: "var(--color-chart-orange)",
  CLASS: "var(--color-chart-violet)",
  PRODUCT: "var(--color-chart-emerald)",
  OTHER: "var(--color-chart-blue)",
};

const SERIES = [
  "var(--color-chart-indigo)",
  "var(--color-chart-teal)",
  "var(--color-chart-orange)",
  "var(--color-chart-violet)",
  "var(--color-chart-emerald)",
  "var(--color-chart-blue)",
];

const TILE: Record<MetricIconName, { bg: string; fg: string; spark: string }> =
  {
    members: {
      bg: "bg-chart-emerald-soft",
      fg: "text-chart-emerald",
      spark: "var(--color-chart-emerald)",
    },
    new: {
      bg: "bg-chart-blue-soft",
      fg: "text-chart-blue",
      spark: "var(--color-chart-blue)",
    },
    check: {
      bg: "bg-chart-violet-soft",
      fg: "text-chart-violet",
      spark: "var(--color-chart-violet)",
    },
    floor: {
      bg: "bg-chart-teal-soft",
      fg: "text-chart-teal",
      spark: "var(--color-chart-teal)",
    },
    revenue: {
      bg: "bg-chart-orange-soft",
      fg: "text-chart-orange",
      spark: "var(--color-chart-orange)",
    },
  };

const PRODUCT_TILES = [
  "bg-chart-emerald-soft text-chart-emerald",
  "bg-chart-violet-soft text-chart-violet",
  "bg-chart-blue-soft text-chart-blue",
  "bg-chart-orange-soft text-chart-orange",
  "bg-chart-teal-soft text-chart-teal",
  "bg-chart-indigo-soft text-chart-indigo",
];

const CARD = "min-w-0 rounded-theme-lg border border-border bg-surface p-5";

const HERO_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
  detail: string;
  icon: MetricIconName;
  spark?: "attendance" | "members";
}[] = [
  {
    key: "active_members",
    label: "Active members",
    detail: "Current active base",
    icon: "members",
  },
  {
    key: "new_members",
    label: "New members",
    detail: "Joined in this period",
    icon: "new",
    spark: "members",
  },
  {
    key: "check_ins",
    label: "Check-ins",
    detail: "Visits in this period",
    icon: "check",
    spark: "attendance",
  },
];

const SECONDARY_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
  detail: string;
}[] = [
  {
    key: "expiring_within_3_days",
    label: "Expiring in 3 days",
    detail: "Within 3 days",
  },
  {
    key: "expiring_within_7_days",
    label: "Expiring in 7 days",
    detail: "Within 7 days",
  },
  {
    key: "expiring_within_30_days",
    label: "Expiring in 30 days",
    detail: "Within 30 days",
  },
  {
    key: "pending_approvals",
    label: "Pending approvals",
    detail: "Awaiting a decision",
  },
  {
    key: "expired_memberships",
    label: "Expired memberships",
    detail: "Ended in this period",
  },
  {
    key: "unique_attendees",
    label: "Unique attendees",
    detail: "Distinct people in this period",
  },
];

type MetricIconName = "check" | "floor" | "members" | "new" | "revenue";

type AttendanceDay = { date: string; check_ins: number };
type ActivityDay = {
  date: string;
  new_members: number;
  total_revenue: string;
};
type MonthScope = "previous" | "range";
type MonthSeries = {
  year: number;
  month: number;
  scope: MonthScope;
  rows: ActivityDay[];
};
type SeriesState =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "empty" }
  | {
      status: "ready";
      points: ChartPoint[];
      note?: string | undefined;
      weekly: boolean;
    };

export function OwnerDashboard() {
  const tx = useTx();
  const preview = useChartPreview();
  const [preset, setPreset] = useState<DashboardRangePreset>("today");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [query, setQuery] = useState("range=today");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [attendance, setAttendance] = useState<SeriesState>({
    status: "loading",
  });
  const [members, setMembers] = useState<SeriesState>({ status: "loading" });
  const [revenue, setRevenue] = useState<SeriesState>({ status: "loading" });
  const [monthly, setMonthly] = useState<SeriesState>({ status: "loading" });
  const [sourceActive, setSourceActive] = useState<number | null>(null);
  const [expenseActive, setExpenseActive] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setDashboard(await operationGet<DashboardData>(`dashboard/?${query}`));
      setError("");
    } catch (caught) {
      setDashboard(null);
      setError(
        caught instanceof Error ? caught.message : "Unable to load dashboard.",
      );
    }
  }, [query]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!dashboard) return;
    let cancelled = false;
    const { date_from: from, date_to: to } = dashboard.range;
    const includeFinance = dashboard.financial !== null;
    setAttendance({ status: "loading" });
    setMembers(
      includeFinance ? { status: "loading" } : { status: "unavailable" },
    );
    setRevenue(
      includeFinance ? { status: "loading" } : { status: "unavailable" },
    );
    setMonthly(
      includeFinance ? { status: "loading" } : { status: "unavailable" },
    );
    const weekCaption = (start: string) =>
      tx("Week of {date}", { date: start });

    void (async () => {
      try {
        const report = await operationGet<unknown>(
          `reports/check-ins/?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`,
        );
        const rows = attendanceDays(rowsOf(report));
        if (cancelled) return;
        setAttendance(attendanceState(rows, from, to, weekCaption));
      } catch {
        if (!cancelled) setAttendance({ status: "unavailable" });
      }
    })();

    if (includeFinance) {
      void (async () => {
        const months = monthRequests(from, to);
        const results = await Promise.all(
          months.map(async (item) => {
            try {
              const report = await operationGet<unknown>(
                `reports/monthly/?year=${item.year}&month=${item.month}`,
              );
              const rows = activityDays(rowsOf(report));
              return rows ? { ...item, rows } : null;
            } catch {
              return null;
            }
          }),
        );
        if (cancelled) return;
        const ready = results.filter(
          (item): item is MonthSeries => item !== null,
        );
        if (!ready.length) {
          setMembers({ status: "unavailable" });
          setRevenue({ status: "unavailable" });
          setMonthly({ status: "unavailable" });
          return;
        }
        const ranged = ready
          .filter((item) => item.scope === "range")
          .flatMap((item) =>
            item.rows.filter(
              (row) =>
                row.date >= from &&
                row.date <= to &&
                row.date.startsWith(monthKey(item.year, item.month)),
            ),
          );
        const memberDays = ranged.map((row) => ({
          date: row.date,
          value: row.new_members,
        }));
        const revenueDays = ranged.map((row) => ({
          date: row.date,
          value: moneyCents(row.total_revenue),
        }));
        setMembers(countState(memberDays, weekCaption));
        setRevenue(countState(revenueDays, weekCaption));
        setMonthly(monthlyState(ready, from, to));
      })();
    }

    return () => {
      cancelled = true;
    };
  }, [dashboard, tx]);

  function selectPreset(value: DashboardRangePreset) {
    setPreset(value);
    setQuery(`range=${value}`);
  }

  function applyCustomRange() {
    if (!customFrom || !customTo) return;
    setQuery(
      `date_from=${encodeURIComponent(customFrom)}&date_to=${encodeURIComponent(customTo)}`,
    );
  }

  if (error) {
    return (
      <div className="error" role="alert">
        <p className="font-semibold text-danger-text">
          {tx("Dashboard unavailable")}
        </p>
        <p className="mt-1">{tx(error)}</p>
        <button
          className="button mt-4"
          onClick={() => void load()}
          type="button"
        >
          {tx("Try again")}
        </button>
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div className="grid w-full gap-4 py-8" role="status">
        <LoadingMark className="mx-auto" />
        <p className="text-center text-sm font-medium text-muted">
          {tx("Loading dashboard…")}
        </p>
        <div className="h-28 animate-pulse rounded-theme-lg border border-border bg-surface motion-reduce:animate-none" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div
              className="h-28 animate-pulse rounded-theme-lg border border-border bg-surface motion-reduce:animate-none"
              key={item}
            />
          ))}
        </div>
        <div className="h-40 animate-pulse rounded-theme-lg border border-border bg-surface motion-reduce:animate-none" />
      </div>
    );
  }

  const money = (value: string) => `${value} ${tx(dashboard.currency)}`;
  const financial = dashboard.financial;
  const memberSeries = preview ? PREVIEW_MEMBERS : members;
  const attendanceSeries = preview ? PREVIEW_CHECKINS : attendance;
  const revenueSeries = preview ? PREVIEW_REVENUE : revenue;
  const monthlySeries = preview ? PREVIEW_MONTHLY : monthly;
  const revenueSources =
    financial && preview
      ? financial.revenue_sources_ranked.map((item) => ({
          ...item,
          amount: PREVIEW_SOURCE_AMOUNT[item.source] ?? item.amount,
        }))
      : financial?.revenue_sources_ranked;
  const expenseRows =
    financial && preview
      ? financial.expenses_by_category.map((item, index) => ({
          ...item,
          amount: PREVIEW_EXPENSE_AMOUNT[index] ?? "0.00",
        }))
      : financial?.expenses_by_category;
  const sameDay = dashboard.range.date_from === dashboard.range.date_to;
  const currency = tx(dashboard.currency);
  const secondaryMetrics = financial
    ? [
        {
          key: "currently_checked_in" as const,
          label: "Currently checked in",
          detail: "In the gym now",
        },
        ...SECONDARY_METRICS,
      ]
    : SECONDARY_METRICS;

  return (
    <div className="grid gap-6">
      {preview ? (
        <p className="rounded-theme-md border border-warning-line bg-warning-soft px-4 py-3 text-body text-warning-text">
          <strong className="font-semibold">{tx("Chart preview")}</strong>
          <span className="mt-1 block text-caption">
            {tx("Sample shapes, not this period's figures.")}
          </span>
        </p>
      ) : null}
      <section className={CARD}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <p className="text-label text-muted">{tx("Reporting period")}</p>
            <div
              aria-label={tx("Dashboard range")}
              className="mt-2 grid w-full grid-cols-2 gap-1 rounded-theme-md bg-inset p-1 sm:flex sm:w-auto sm:flex-wrap"
              role="group"
            >
              {PRESETS.map((item) => {
                const selected =
                  preset === item.value && query.startsWith("range=");
                return (
                  <button
                    aria-pressed={selected}
                    className={
                      selected
                        ? "min-h-11 rounded-theme-sm border border-border bg-surface px-3 text-sm font-semibold text-primary-hover sm:shrink-0"
                        : "min-h-11 rounded-theme-sm border border-transparent px-3 text-sm font-medium text-text-secondary hover:bg-hover sm:shrink-0"
                    }
                    key={item.value}
                    onClick={() => selectPreset(item.value)}
                    type="button"
                  >
                    {tx(item.label)}
                  </button>
                );
              })}
            </div>
          </div>
          <form
            className="grid gap-3 sm:flex sm:flex-wrap sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              applyCustomRange();
            }}
          >
            <label className="grid gap-1 text-label text-muted sm:w-40">
              {tx("From")}
              <input
                className="input"
                onChange={(event) => setCustomFrom(event.target.value)}
                type="date"
                value={customFrom}
              />
            </label>
            <label className="grid gap-1 text-label text-muted sm:w-40">
              {tx("To")}
              <input
                className="input"
                onChange={(event) => setCustomTo(event.target.value)}
                type="date"
                value={customTo}
              />
            </label>
            <button
              className="button w-full sm:w-auto"
              disabled={!customFrom || !customTo}
              type="submit"
            >
              {tx("Apply custom range")}
            </button>
          </form>
        </div>
        <p className="mt-3 text-caption text-muted">
          <span>
            {`${dashboard.range.date_from} ${tx("to")} ${dashboard.range.date_to}`}
          </span>
          <span className="px-2 text-faint"> · </span>
          <span>{dashboard.timezone}</span>
          <span className="px-2 text-faint"> · </span>
          <span>{currency}</span>
        </p>
      </section>

      <section aria-labelledby="dashboard-operational" className="grid gap-4">
        <div>
          <h2 className="text-title text-text" id="dashboard-operational">
            {tx("Operational totals")}
          </h2>
          <p className="mt-1 max-w-[640px] text-caption text-muted">
            {tx("Expiring windows overlap and are not shares of one total.")}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {HERO_METRICS.map((metric) => (
            <Stat
              detail={
                metric.key === "check_ins" && sameDay
                  ? "Visits on this day"
                  : metric.detail
              }
              icon={metric.icon}
              key={metric.key}
              label={metric.label}
              spark={
                metric.spark === "members"
                  ? sparkOf(memberSeries)
                  : metric.spark === "attendance"
                    ? sparkOf(attendanceSeries)
                    : undefined
              }
              value={String(dashboard.operational[metric.key])}
            />
          ))}
          {financial ? (
            <Stat
              detail="Collected in this period"
              icon="revenue"
              label="Total revenue"
              spark={sparkOf(revenueSeries)}
              value={money(financial.total_revenue)}
            />
          ) : (
            <Stat
              detail="In the gym now"
              icon="floor"
              label="Currently checked in"
              value={String(dashboard.operational.currently_checked_in)}
            />
          )}
          <article
            className={`${CARD} flex h-full items-center justify-around gap-2`}
          >
            <div className="grid justify-items-center">
              <ArcGauge
                max={dashboard.operational.active_members}
                value={dashboard.operational.currently_checked_in}
              />
              <p className="text-caption text-muted">{tx("In the gym now")}</p>
            </div>
            {financial ? (
              <ShareDonut
                active={null}
                parts={(revenueSources ?? []).map((item) => ({
                  color: sourceColor(item.source, amountOf(item.amount)),
                  value: Math.max(amountOf(item.amount), 0),
                }))}
                size="sm"
              />
            ) : null}
          </article>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {secondaryMetrics.map((metric) => (
            <QuietStat
              detail={metric.detail}
              key={metric.key}
              label={metric.label}
              tone={operationalTone(
                metric.key,
                dashboard.operational[metric.key],
              )}
              value={String(dashboard.operational[metric.key])}
            />
          ))}
          <QuietStat
            detail="Not included in this report."
            label="Total members"
            tone="text"
          />
        </div>
      </section>

      {financial === null ? (
        <p className={`${CARD} text-body text-muted`}>
          {tx("Financial totals are not included in this response.")}
        </p>
      ) : (
        <section aria-labelledby="dashboard-financial" className="grid gap-4">
          <div className="grid gap-4 xl:grid-cols-3">
            <MemberChart className="xl:col-span-2" state={memberSeries} />
            <RevenueMix
              active={sourceActive}
              money={money}
              onActive={setSourceActive}
              sources={revenueSources ?? []}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
            <section className={`${CARD} xl:order-1`}>
              <h2
                className="mb-2 text-title text-text"
                id="dashboard-financial"
              >
                {tx("Financial totals")}
              </h2>
              <div className="divide-y divide-border">
                <MoneyLine
                  label="Total revenue"
                  value={money(financial.total_revenue)}
                />
                <MoneyLine
                  hint={tx("{count} members", {
                    count: financial.pending_payment_member_count,
                  })}
                  label="Pending payments"
                  value={money(financial.pending_payment_amount)}
                />
                <MoneyLine
                  label="Total expenses"
                  value={money(financial.total_expenses)}
                />
                <MoneyLine
                  label="Staff salary"
                  nested
                  value={money(financial.staff_salary_expense)}
                />
                <MoneyLine
                  label="PT commission"
                  nested
                  value={money(financial.pt_commission_expense)}
                />
              </div>
              <div className="mt-1 border-t border-border pt-3">
                <div className="flex items-center justify-between gap-4">
                  <p className="text-body-strong text-text">
                    {tx("Net profit")}
                  </p>
                  <strong
                    className={`text-body-strong tabular-nums ${
                      amountOf(financial.net_profit) < 0
                        ? "text-danger-text"
                        : "text-text"
                    }`}
                  >
                    {money(financial.net_profit)}
                  </strong>
                </div>
              </div>
            </section>
            <CheckinChart className="xl:order-3" state={attendanceSeries} />
            <DashboardPanel className="xl:order-4" title="Expense overview">
              <div className="grid items-center gap-4">
                <ShareDonut
                  active={expenseActive}
                  parts={(expenseRows ?? []).map((item, index) => ({
                    color: amountColor(
                      amountOf(item.amount),
                      seriesColor(index),
                    ),
                    value: Math.max(amountOf(item.amount), 0),
                  }))}
                />
                <ul className="grid gap-1">
                  {(expenseRows ?? []).map((item, index) => {
                    const amount = amountOf(item.amount);
                    return (
                      <li
                        className={`rounded-theme-md px-2 py-2 outline-none ${
                          expenseActive === index ? "bg-inset" : ""
                        }`}
                        key={item.system_key}
                        onBlur={() => setExpenseActive(null)}
                        onFocus={() => setExpenseActive(index)}
                        onMouseEnter={() => setExpenseActive(index)}
                        onMouseLeave={() => setExpenseActive(null)}
                        tabIndex={0}
                      >
                        <div className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2 text-sm">
                          <span
                            aria-hidden="true"
                            className="size-2.5 rounded-full"
                            style={{
                              background: amountColor(
                                amount,
                                seriesColor(index),
                              ),
                            }}
                          />
                          <span className="min-w-0 text-text-secondary">
                            {item.system_key}
                          </span>
                          <strong className="col-start-2 text-text tabular-nums">
                            {money(item.amount)}
                          </strong>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </DashboardPanel>
            <DashboardPanel className="xl:order-2" title="Top products">
              {financial.top_product_items.length ? (
                <ul className="divide-y divide-border">
                  {financial.top_product_items.map((item, index) => (
                    <li
                      className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
                      key={item.product_id}
                    >
                      <span className="flex min-w-0 items-start gap-3">
                        <span
                          className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-theme-md ${PRODUCT_TILES[index % PRODUCT_TILES.length]}`}
                        >
                          <ProductMark />
                        </span>
                        <span className="min-w-0">
                          <strong className="block text-body-strong text-text">
                            {item.name}
                          </strong>
                          <span className="mt-1 block text-caption text-muted">
                            {item.quantity_sold} {tx("sold")}
                          </span>
                        </span>
                      </span>
                      <span className="shrink-0 text-right text-caption text-muted">
                        <strong className="block text-body-strong text-text tabular-nums">
                          {money(item.revenue)}
                        </strong>
                        <span className="tabular-nums">
                          {money(item.gross_profit)} {tx("profit")}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body text-muted">
                  {tx("No product sales in this period.")}
                </p>
              )}
            </DashboardPanel>
          </div>
        </section>
      )}

      <section aria-label={tx("Trends")} className="grid gap-4 lg:grid-cols-2">
        {financial ? null : (
          <>
            <MemberChart state={memberSeries} />
            <CheckinChart state={attendanceSeries} />
          </>
        )}
        <ChartCard
          note={revenue.status === "ready" ? revenue.note : undefined}
          title={
            revenue.status === "ready" && revenue.weekly
              ? "Revenue by week"
              : "Revenue by day"
          }
        >
          <SeriesBody
            accent="var(--color-chart-orange)"
            empty="Daily revenue is not included in this response."
            formatTick={moneyTick}
            formatValue={(value) => formatMoneyCents(value, currency)}
            kind="area"
            recordedEmpty="No revenue was recorded for this period."
            series="Total revenue"
            state={revenueSeries}
          />
        </ChartCard>
        <ChartCard
          note={
            monthly.status === "ready"
              ? "Revenue inside the selected range, summed by month."
              : undefined
          }
          title="Monthly revenue"
        >
          <SeriesBody
            accent="var(--color-chart-emerald)"
            empty="Monthly revenue is not included in this response."
            formatTick={moneyTick}
            formatValue={(value) => formatMoneyCents(value, currency)}
            kind="bar"
            recordedEmpty="No revenue was recorded for this period."
            series="Monthly revenue"
            state={monthlySeries}
          />
        </ChartCard>
        <section className={`${CARD} lg:col-span-2`}>
          <h3 className="text-title text-text">{tx("Membership status")}</h3>
          <p className="mt-4 text-body text-muted">
            {tx("Membership status is not included in this report.")}
          </p>
        </section>
      </section>
    </div>
  );
}

function MemberChart({
  className,
  state,
}: {
  className?: string | undefined;
  state: SeriesState;
}) {
  const weekly = state.status === "ready" && state.weekly;
  return (
    <ChartCard
      className={className}
      note={state.status === "ready" ? state.note : undefined}
      title={weekly ? "New members by week" : "New members by day"}
    >
      <SeriesBody
        accent="var(--color-chart-teal)"
        bands
        empty="Daily member totals are not included in this response."
        formatValue={(value) => String(value)}
        height={272}
        kind="area"
        recordedEmpty="No new members were recorded for this period."
        series="New members"
        state={state}
      />
    </ChartCard>
  );
}

function CheckinChart({
  className,
  state,
}: {
  className?: string | undefined;
  state: SeriesState;
}) {
  const weekly = state.status === "ready" && state.weekly;
  return (
    <ChartCard
      className={className}
      note={state.status === "ready" ? state.note : undefined}
      title={weekly ? "Check-ins by week" : "Check-ins by day"}
    >
      <SeriesBody
        accent="var(--color-chart-violet)"
        empty="Daily attendance is not included in this response."
        formatValue={(value) => String(value)}
        height={220}
        kind="bar"
        recordedEmpty="No check-ins in this period."
        series="Check-ins"
        state={state}
        variedBars
      />
    </ChartCard>
  );
}

function RevenueMix({
  active,
  money,
  onActive,
  sources,
}: {
  active: number | null;
  money: (value: string) => string;
  onActive: (index: number | null) => void;
  sources: { source: string; amount: string }[];
}) {
  return (
    <DashboardPanel title="Revenue sources">
      <div className="grid items-center gap-4">
        <ShareDonut
          active={active}
          parts={sources.map((item) => ({
            color: sourceColor(item.source, amountOf(item.amount)),
            value: Math.max(amountOf(item.amount), 0),
          }))}
        />
        <ol className="grid gap-1">
          {sources.map((item, index) => {
            const amount = amountOf(item.amount);
            return (
              <li
                className={`rounded-theme-md px-2 py-2 outline-none ${
                  active === index ? "bg-inset" : ""
                }`}
                key={item.source}
                onBlur={() => onActive(null)}
                onFocus={() => onActive(index)}
                onMouseEnter={() => onActive(index)}
                onMouseLeave={() => onActive(null)}
                tabIndex={0}
              >
                <div className="flex items-center justify-between gap-4 text-sm">
                  <span className="flex min-w-0 items-center gap-2 font-medium text-text">
                    <span
                      aria-hidden="true"
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ background: sourceColor(item.source, amount) }}
                    />
                    <span className="w-5 text-caption text-muted tabular-nums">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    {SOURCE_LABELS[item.source] ?? item.source}
                  </span>
                  <strong className="shrink-0 text-text tabular-nums">
                    {money(item.amount)}
                  </strong>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </DashboardPanel>
  );
}

function ProductMark() {
  return (
    <svg aria-hidden="true" className="size-4" fill="none" viewBox="0 0 20 20">
      <path
        d="M4.75 7.5 10 4.75 15.25 7.5 10 10.25 4.75 7.5Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.75"
      />
      <path
        d="M4.75 7.5V12.5L10 15.25 15.25 12.5V7.5"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.75"
      />
    </svg>
  );
}

function amountOf(value: string): number {
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : 0;
}

function sourceColor(source: string, amount: number): string {
  if (amount < 0) return "var(--color-danger)";
  if (amount === 0) return "var(--color-border-strong)";
  return SOURCE_COLORS[source] ?? "var(--color-muted)";
}

function seriesColor(index: number): string {
  return SERIES[index % SERIES.length] ?? "var(--color-primary)";
}

function amountColor(amount: number, color: string): string {
  if (amount < 0) return "var(--color-danger)";
  if (amount === 0) return "var(--color-border-strong)";
  return color;
}

function attendanceDays(
  rows: Record<string, unknown>[] | null,
): AttendanceDay[] | null {
  if (!rows) return null;
  const days: AttendanceDay[] = [];
  for (const row of rows) {
    if (typeof row.date !== "string" || typeof row.check_ins !== "number") {
      return null;
    }
    days.push({ date: row.date, check_ins: row.check_ins });
  }
  return days;
}

function activityDays(
  rows: Record<string, unknown>[] | null,
): ActivityDay[] | null {
  if (!rows) return null;
  const days: ActivityDay[] = [];
  for (const row of rows) {
    if (
      typeof row.date !== "string" ||
      typeof row.new_members !== "number" ||
      typeof row.total_revenue !== "string"
    ) {
      return null;
    }
    days.push({
      date: row.date,
      new_members: row.new_members,
      total_revenue: row.total_revenue,
    });
  }
  return days;
}

function monthRequests(
  from: string,
  to: string,
): { year: number; month: number; scope: MonthScope }[] {
  const months = monthsCovering(from, to).map((item) => ({
    ...item,
    scope: "range" as const,
  }));
  const only = months[0];
  if (months.length === 1 && only && from.endsWith("-01") && isMonthEnd(to)) {
    return [
      { ...previousMonth(only.year, only.month), scope: "previous" },
      ...months,
    ];
  }
  return months;
}

function attendanceState(
  rows: AttendanceDay[] | null,
  from: string,
  to: string,
  weekCaption: (start: string) => string,
): SeriesState {
  if (!rows) return { status: "unavailable" };
  const days = rows
    .filter((row) => row.date >= from && row.date <= to)
    .map((row) => ({ date: row.date, value: row.check_ins }));
  if (!days.length) return { status: "empty" };
  return readyPoints(days, weekCaption);
}

function countState(
  days: { date: string; value: number }[],
  weekCaption: (start: string) => string,
): SeriesState {
  if (!days.length) return { status: "empty" };
  return readyPoints(days, weekCaption);
}

function readyPoints(
  days: { date: string; value: number }[],
  weekCaption: (start: string) => string,
): SeriesState {
  const weekly = shouldBucketWeekly(days.length);
  const points = toChartPoints(days, weekly, weekCaption);
  if (points.every((point) => point.value === 0)) return { status: "empty" };
  return {
    status: "ready",
    weekly,
    note: weekly ? "Weekly totals" : undefined,
    points,
  };
}

function monthlyState(
  months: MonthSeries[],
  from: string,
  to: string,
): SeriesState {
  const points = months.flatMap((item) => {
    const rows =
      item.scope === "previous"
        ? item.rows.filter((row) =>
            row.date.startsWith(monthKey(item.year, item.month)),
          )
        : item.rows.filter(
            (row) =>
              row.date >= from &&
              row.date <= to &&
              row.date.startsWith(monthKey(item.year, item.month)),
          );
    if (!rows.length) return [];
    const key = monthKey(item.year, item.month);
    return [
      {
        label: key,
        caption: key,
        value: rows.reduce(
          (sum, row) => sum + moneyCents(row.total_revenue),
          0,
        ),
      },
    ];
  });
  if (!points.length || points.every((point) => point.value === 0)) {
    return { status: "empty" };
  }
  return { status: "ready", points, weekly: false };
}

const PREVIEW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const PREVIEW_SOURCE_AMOUNT: Record<string, string> = {
  NEW_MEMBER: "300.00",
  RENEWAL: "15.00",
  PT: "20.00",
  CLASS: "40.00",
  PRODUCT: "80.00",
  OTHER: "0.00",
};

const PREVIEW_EXPENSE_AMOUNT = [
  "300.00",
  "15.00",
  "20.00",
  "80.00",
  "45.00",
  "10.00",
];

const PREVIEW_MEMBERS = previewSeries([
  2, 6, 11, 16, 22, 27, 24, 20, 26, 33, 37, 31, 24, 18,
]);
const PREVIEW_CHECKINS = previewSeries([128, 86, 154, 176, 112, 148, 92]);
const PREVIEW_REVENUE = previewSeries([
  400, 900, 1400, 2100, 1800, 2600, 2200, 1600, 2800, 3400, 3000, 1900, 2400,
  1500,
]);
const PREVIEW_MONTHLY = previewSeries(
  [12000, 9800, 15400, 17600, 13200, 16800],
  ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
);

function previewSeries(values: number[], labels = PREVIEW_LABELS): SeriesState {
  return {
    status: "ready",
    weekly: false,
    points: values.map((value, index) => ({
      label: labels[index % labels.length] ?? String(index + 1),
      caption: `Sample ${index + 1}`,
      value,
    })),
  };
}

function useChartPreview(): boolean {
  return useSyncExternalStore(
    () => () => undefined,
    () => {
      if (process.env.NODE_ENV === "test") return false;
      const flag = new URLSearchParams(window.location.search).get("charts");
      if (flag === "off") return false;
      if (flag === "preview") return true;
      const host = window.location.hostname;
      if (host === "localhost" || host === "127.0.0.1") return true;
      return process.env.NODE_ENV !== "production";
    },
    () => false,
  );
}

function sparkOf(state: SeriesState): number[] | undefined {
  if (state.status !== "ready" || state.points.length < 2) return undefined;
  return state.points.map((point) => point.value);
}

function operationalTone(
  key: keyof DashboardData["operational"],
  value: number,
): "danger" | "text" | "warning" {
  if (value <= 0) return "text";
  if (key === "expired_memberships") return "danger";
  if (
    key === "expiring_within_3_days" ||
    key === "expiring_within_7_days" ||
    key === "expiring_within_30_days" ||
    key === "pending_approvals"
  ) {
    return "warning";
  }
  return "text";
}

function Stat({
  detail,
  icon,
  label,
  spark,
  value,
}: {
  detail?: string;
  icon: MetricIconName;
  label: string;
  spark?: number[] | undefined;
  value: string;
}) {
  const tx = useTx();
  const tile = TILE[icon];
  return (
    <article className={`${CARD} flex h-full flex-col`}>
      <span
        className={`flex size-8 items-center justify-center rounded-theme-md ${tile.bg} ${tile.fg}`}
      >
        <MetricGlyph name={icon} />
      </span>
      <p className="mt-3 min-h-10 text-label text-muted">{tx(label)}</p>
      <p className="mt-1 text-display text-text tabular-nums">{value}</p>
      <div className="mt-auto flex items-end justify-between gap-3 pt-3">
        {detail ? (
          <p className="text-caption text-muted">{tx(detail)}</p>
        ) : (
          <span />
        )}
        {spark ? (
          <Sparkline color={tile.spark} values={spark} />
        ) : (
          <span className="h-8 w-24" />
        )}
      </div>
    </article>
  );
}

function QuietStat({
  detail,
  label,
  tone,
  value,
}: {
  detail: string;
  label: string;
  tone: "danger" | "text" | "warning";
  value?: string | undefined;
}) {
  const tx = useTx();
  const surface =
    tone === "danger"
      ? "border-danger-line bg-danger-soft text-danger-text"
      : "border-warning-line bg-warning-soft text-warning-text";
  return (
    <div className={`min-w-0 rounded-theme-md border px-4 py-3 ${surface}`}>
      <p className="text-body font-semibold">{tx(label)}</p>
      {value !== undefined ? (
        <p className="mt-1 text-title tabular-nums">{value}</p>
      ) : null}
      <p className="mt-1 text-caption">{tx(detail)}</p>
    </div>
  );
}

function MoneyLine({
  hint,
  label,
  nested = false,
  value,
}: {
  hint?: string | undefined;
  label: string;
  nested?: boolean | undefined;
  value: string;
}) {
  const tx = useTx();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-3">
      <p
        className={
          nested ? "pl-3 text-body text-muted" : "text-body text-text-secondary"
        }
      >
        {tx(label)}
      </p>
      {hint ? (
        <p className="col-start-1 text-caption text-muted">{hint}</p>
      ) : null}
      <strong className="row-span-2 self-center text-body-strong text-text tabular-nums">
        {value}
      </strong>
    </div>
  );
}

function ChartCard({
  children,
  className,
  note,
  title,
}: {
  children: ReactNode;
  className?: string | undefined;
  note?: string | undefined;
  title: string;
}) {
  const tx = useTx();
  return (
    <section className={className ? `${CARD} ${className}` : CARD}>
      <h3 className="text-title text-text">{tx(title)}</h3>
      {note ? <p className="mt-1 text-caption text-muted">{tx(note)}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function moneyTick(cents: number): string {
  const major = cents / 100;
  return Number.isInteger(major) ? String(major) : major.toFixed(2);
}

function SeriesBody({
  accent,
  bands = false,
  empty,
  formatTick,
  formatValue,
  height,
  kind,
  recordedEmpty,
  series,
  state,
  variedBars = false,
}: {
  accent?: string | undefined;
  bands?: boolean;
  empty: string;
  formatTick?: ((value: number) => string) | undefined;
  formatValue: (value: number) => string;
  height?: number | undefined;
  kind: "area" | "bar" | "line";
  recordedEmpty: string;
  series: string;
  state: SeriesState;
  variedBars?: boolean;
}) {
  const tx = useTx();
  if (state.status === "loading") {
    return (
      <div
        aria-busy="true"
        className="h-56 animate-pulse rounded-theme-md bg-inset motion-reduce:animate-none"
      >
        <span className="sr-only">{tx("Loading report…")}</span>
      </div>
    );
  }
  if (state.status === "unavailable") {
    return (
      <p className="flex min-h-56 items-center text-body text-muted">
        {tx(empty)}
      </p>
    );
  }
  if (state.status === "empty") {
    return (
      <p className="flex min-h-56 items-center text-body text-muted">
        {tx(recordedEmpty)}
      </p>
    );
  }
  return (
    <TrendChart
      accent={accent ?? "var(--color-chart-teal)"}
      bands={bands}
      empty={recordedEmpty}
      formatTick={formatTick}
      formatValue={formatValue}
      height={height ?? 224}
      key={`${series}-${state.points.length}-${state.points[0]?.caption ?? ""}`}
      kind={kind}
      points={state.points}
      series={series}
      variedBars={variedBars}
    />
  );
}

function MetricGlyph({ name }: { name: MetricIconName }) {
  return (
    <svg
      aria-hidden="true"
      className="size-4 shrink-0"
      fill="none"
      viewBox="0 0 20 20"
    >
      {name === "members" ? (
        <path
          d="M10 10.25a2.75 2.75 0 1 0 0-5.5 2.75 2.75 0 0 0 0 5.5ZM4.75 16.25a5.25 5.25 0 0 1 10.5 0"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
      ) : null}
      {name === "new" ? (
        <path
          d="M10 4.5v11M5.5 8.5 10 4.5l4.5 4"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
      ) : null}
      {name === "check" ? (
        <path
          d="M4.75 10.25 8 13.5l7.25-7.5"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
      ) : null}
      {name === "floor" ? (
        <path
          d="M10 16.25a6.25 6.25 0 1 0 0-12.5 6.25 6.25 0 0 0 0 12.5ZM10 6.5v3.75l2.25 1.5"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
      ) : null}
      {name === "revenue" ? (
        <path
          d="M10 4.75v10.5M7.25 7.25c.4-1 1.4-1.6 2.75-1.6 1.6 0 2.75.8 2.75 2.05S11.7 9.7 10 9.7 7.25 10.5 7.25 11.8s1.2 2.05 2.75 2.05c1.35 0 2.4-.6 2.8-1.6"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
      ) : null}
    </svg>
  );
}

function DashboardPanel({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string | undefined;
  title: string;
}) {
  const tx = useTx();
  return (
    <section className={className ? `${CARD} ${className}` : CARD}>
      <h3 className="mb-4 text-title text-text">{tx(title)}</h3>
      {children}
    </section>
  );
}

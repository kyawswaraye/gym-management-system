"use client";
import { useTx } from "@/i18n/use-tx";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { operationGet } from "@/features/operations/client";
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
  NEW_MEMBER: "#0866ff",
  RENEWAL: "#6aa6ff",
  PT: "#0f766e",
  CLASS: "#1f9d4d",
  PRODUCT: "#e6a100",
  OTHER: "#c43b55",
};

const SERIES = [
  "#0866ff",
  "#1f9d4d",
  "#e6a100",
  "#0f766e",
  "#c43b55",
  "#6d5b8a",
  "#6aa6ff",
  "#e23b4a",
  "#b5812b",
  "#3d8f8a",
];

const CARD =
  "rounded-3xl bg-white shadow-[0_18px_50px_-32px_rgba(7,24,51,0.65)] ring-1 ring-[#e6ebf2]";

const OPERATIONAL_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
  detail: string;
  glyph: GlyphName;
}[] = [
  {
    key: "active_members",
    label: "Active members",
    detail: "Current active base",
    glyph: "members",
  },
  {
    key: "check_ins",
    label: "Check-ins",
    detail: "Visits in this period",
    glyph: "check",
  },
  {
    key: "new_members",
    label: "New members",
    detail: "Joined in this period",
    glyph: "join",
  },
  {
    key: "currently_checked_in",
    label: "In the gym now",
    detail: "Currently checked in",
    glyph: "pulse",
  },
];

const MEMBERSHIP_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
  color: string;
}[] = [
  { key: "unique_attendees", label: "Unique attendees", color: "#0866ff" },
  {
    key: "expired_memberships",
    label: "Expired memberships",
    color: "#c43b55",
  },
  {
    key: "expiring_within_3_days",
    label: "Expiring in 3 days",
    color: "#e23b4a",
  },
  {
    key: "expiring_within_7_days",
    label: "Expiring in 7 days",
    color: "#e6a100",
  },
  {
    key: "expiring_within_30_days",
    label: "Expiring in 30 days",
    color: "#6aa6ff",
  },
  { key: "pending_approvals", label: "Pending approvals", color: "#6d5b8a" },
];

type GlyphName =
  | "members"
  | "check"
  | "join"
  | "pulse"
  | "revenue"
  | "clock"
  | "expense"
  | "profit";

type MoneyTone = "revenue" | "pending" | "expense" | "profit";

export function OwnerDashboard() {
  const tx = useTx();
  const [preset, setPreset] = useState<DashboardRangePreset>("today");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [query, setQuery] = useState("range=today");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

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
      <div
        className="rounded-3xl border border-red-200 bg-red-50 p-5"
        role="alert"
      >
        <p className="font-semibold text-red-900">
          {tx("Dashboard unavailable")}
        </p>
        <p className="mt-1 text-sm text-red-700">{tx(error)}</p>
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
      <div className="grid gap-6" role="status">
        <span className="sr-only">{tx("Loading dashboard…")}</span>
        <div className="h-28 animate-pulse rounded-3xl bg-white/80" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div
              className="h-40 animate-pulse rounded-3xl bg-white/80"
              key={item}
            />
          ))}
        </div>
        <div className="h-72 animate-pulse rounded-3xl bg-[#071833]/10" />
      </div>
    );
  }

  const money = (value: string) => `${value} ${tx(dashboard.currency)}`;
  const financial = dashboard.financial;
  const activeMembers = dashboard.operational.active_members;
  const onFloor = dashboard.operational.currently_checked_in;
  const floorRatio =
    activeMembers > 0 ? Math.min(1, onFloor / activeMembers) : 0;
  const membershipPeak = peak(
    MEMBERSHIP_METRICS.map((metric) => dashboard.operational[metric.key]),
  );
  const moneyPeak = financial
    ? peak([
        amountOf(financial.total_revenue),
        amountOf(financial.pending_payment_amount),
        amountOf(financial.total_expenses),
        amountOf(financial.net_profit),
      ])
    : 0;

  return (
    <div className="grid gap-8">
      <section className={`${CARD} p-4 sm:p-5`}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#0866ff]">
              {tx("Reporting period")}
            </p>
            <div
              aria-label={tx("Dashboard range")}
              className="mt-3 flex gap-1 overflow-x-auto rounded-2xl bg-[#eef3f9] p-1"
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
                        ? "shrink-0 rounded-xl bg-[#0866ff] px-3.5 py-2 text-sm font-semibold text-white shadow-[0_8px_16px_-10px_#0866ff]"
                        : "shrink-0 rounded-xl px-3.5 py-2 text-sm font-medium text-slate-600 hover:bg-white"
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
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              applyCustomRange();
            }}
          >
            <label className="grid gap-1 text-xs font-semibold text-slate-600">
              {tx("From")}
              <input
                className="min-h-10 rounded-xl border border-[#d5deea] bg-[#f7f9fc] px-3 text-sm text-[#071833]"
                onChange={(event) => setCustomFrom(event.target.value)}
                type="date"
                value={customFrom}
              />
            </label>
            <label className="grid gap-1 text-xs font-semibold text-slate-600">
              {tx("To")}
              <input
                className="min-h-10 rounded-xl border border-[#d5deea] bg-[#f7f9fc] px-3 text-sm text-[#071833]"
                onChange={(event) => setCustomTo(event.target.value)}
                type="date"
                value={customTo}
              />
            </label>
            <button
              className="button"
              disabled={!customFrom || !customTo}
              type="submit"
            >
              {tx("Apply custom range")}
            </button>
          </form>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[#eef2f6] pt-4">
          <span className="rounded-full bg-[#eef3f9] px-3 py-1 text-xs font-semibold text-[#334155]">
            {dashboard.range.date_from} {tx("to")} {dashboard.range.date_to}
          </span>
          <span className="rounded-full bg-[#eef3f9] px-3 py-1 text-xs font-semibold text-[#334155]">
            {dashboard.timezone}
          </span>
          <span className="rounded-full bg-[#eef3f9] px-3 py-1 text-xs font-semibold text-[#334155]">
            {tx(dashboard.currency)}
          </span>
        </div>
      </section>

      <section aria-labelledby="dashboard-operational" className="grid gap-4">
        <SectionHeading id="dashboard-operational" title="Operational totals" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {OPERATIONAL_METRICS.map((metric) => (
            <Metric
              detail={metric.detail}
              key={metric.key}
              label={metric.label}
              mark={
                metric.key === "currently_checked_in" ? (
                  <FloorRing ratio={floorRatio} />
                ) : (
                  <span className="grid size-14 place-items-center rounded-2xl bg-[#0866ff] text-white shadow-[0_12px_24px_-14px_#0866ff]">
                    <Glyph name={metric.glyph} />
                  </span>
                )
              }
              value={String(dashboard.operational[metric.key])}
            />
          ))}
        </div>
        <div className={`${CARD} p-5 sm:p-7`}>
          <h3 className="text-lg font-bold text-[#071833]">
            {tx("Member activity")}
          </h3>
          <ul className="mt-2 grid md:grid-cols-2 md:gap-x-12">
            {MEMBERSHIP_METRICS.map((metric) => {
              const value = dashboard.operational[metric.key];
              return (
                <li className="border-b border-[#eef2f6] py-4" key={metric.key}>
                  <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3">
                    <span className="flex min-w-0 items-center gap-2.5 text-sm font-medium text-slate-600">
                      <span
                        aria-hidden="true"
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ background: metric.color }}
                      />
                      {tx(metric.label)}
                    </span>
                    <strong className="text-2xl font-black text-[#071833] tabular-nums">
                      {value}
                    </strong>
                    <div
                      aria-hidden="true"
                      className="col-span-2 h-3 overflow-hidden rounded-full bg-[#eef3f9]"
                    >
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${barPercent(value, membershipPeak)}%`,
                          background: `linear-gradient(90deg, ${metric.color}cc, ${metric.color})`,
                        }}
                      />
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </section>

      {financial === null ? (
        <div className={`${CARD} p-6 text-sm text-slate-600`}>
          {tx("Financial totals are not included in this response.")}
        </div>
      ) : (
        <section aria-labelledby="dashboard-financial" className="grid gap-5">
          <SectionHeading id="dashboard-financial" title="Financial totals" />
          <div className={`${CARD} p-5 sm:p-6`}>
            <div className="relative">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-0 top-0 h-48"
              >
                <div className="absolute inset-x-0 top-0 border-t border-[#e6ebf2]" />
                <div className="absolute inset-x-0 top-1/3 border-t border-[#eef2f6]" />
                <div className="absolute inset-x-0 top-2/3 border-t border-[#eef2f6]" />
                <div className="absolute inset-x-0 bottom-0 border-t border-[#d5deea]" />
              </div>
              <div className="relative grid grid-cols-2 gap-3 sm:grid-cols-4">
                <FinancialMetric
                  amount={financial.total_revenue}
                  label="Total revenue"
                  peak={moneyPeak}
                  tone="revenue"
                  value={money(financial.total_revenue)}
                />
                <FinancialMetric
                  amount={financial.pending_payment_amount}
                  detail={tx("{count} members", {
                    count: financial.pending_payment_member_count,
                  })}
                  label="Pending payments"
                  peak={moneyPeak}
                  tone="pending"
                  value={money(financial.pending_payment_amount)}
                />
                <FinancialMetric
                  amount={financial.total_expenses}
                  label="Total expenses"
                  peak={moneyPeak}
                  tone="expense"
                  value={money(financial.total_expenses)}
                />
                <FinancialMetric
                  amount={financial.net_profit}
                  label="Net profit"
                  peak={moneyPeak}
                  tone="profit"
                  value={money(financial.net_profit)}
                />
              </div>
            </div>
          </div>

          <div className="grid gap-5 xl:grid-cols-2">
            <DashboardPanel title="Revenue sources">
              <div className="grid items-center gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
                <ShareRing
                  parts={financial.revenue_sources_ranked.map((item) => ({
                    color: sourceColor(item.source, amountOf(item.amount)),
                    value: Math.max(amountOf(item.amount), 0),
                  }))}
                />
                <ol className="grid gap-3">
                  {financial.revenue_sources_ranked.map((item, index) => {
                    const amount = amountOf(item.amount);
                    const color = sourceColor(item.source, amount);
                    return (
                      <li className="rounded-2xl px-3 py-2.5" key={item.source}>
                        <div className="flex items-center justify-between gap-4 text-sm">
                          <span className="flex min-w-0 items-center gap-2.5 font-medium text-[#243044]">
                            <span
                              aria-hidden="true"
                              className="size-3 shrink-0 rounded-full"
                              style={{ background: color }}
                            />
                            <span className="w-5 text-xs text-slate-400 tabular-nums">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            {SOURCE_LABELS[item.source] ?? item.source}
                          </span>
                          <strong className="shrink-0 text-[#071833] tabular-nums">
                            {money(item.amount)}
                          </strong>
                        </div>
                        <div
                          aria-hidden="true"
                          className="mt-2 h-2.5 overflow-hidden rounded-full bg-[#eef3f9]"
                        >
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${barPercent(
                                amount,
                                peak(
                                  financial.revenue_sources_ranked.map((row) =>
                                    amountOf(row.amount),
                                  ),
                                ),
                              )}%`,
                              background: `linear-gradient(90deg, ${color}b3, ${color})`,
                            }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </div>
            </DashboardPanel>
            <DashboardPanel title="Expense overview">
              <div className="mb-6 grid grid-cols-2 gap-3">
                <MiniTotal
                  label="Staff salary"
                  value={money(financial.staff_salary_expense)}
                />
                <MiniTotal
                  label="PT commission"
                  value={money(financial.pt_commission_expense)}
                />
              </div>
              <div className="grid items-center gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
                <ShareRing
                  parts={financial.expenses_by_category.map((item, index) => ({
                    color: amountColor(
                      amountOf(item.amount),
                      seriesColor(index),
                    ),
                    value: Math.max(amountOf(item.amount), 0),
                  }))}
                />
                <ul className="grid gap-2.5">
                  {financial.expenses_by_category.map((item, index) => {
                    const amount = amountOf(item.amount);
                    const color = amountColor(amount, seriesColor(index));
                    return (
                      <li key={item.system_key}>
                        <div className="flex items-center justify-between gap-4 text-sm">
                          <span className="flex min-w-0 items-center gap-2.5 text-slate-600">
                            <span
                              aria-hidden="true"
                              className="size-2.5 shrink-0 rounded-full"
                              style={{ background: color }}
                            />
                            <span className="min-w-0 break-all">
                              {item.system_key}
                            </span>
                          </span>
                          <strong className="shrink-0 text-[#071833] tabular-nums">
                            {money(item.amount)}
                          </strong>
                        </div>
                        <div
                          aria-hidden="true"
                          className="mt-1.5 h-2 overflow-hidden rounded-full bg-[#eef3f9]"
                        >
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${barPercent(
                                amount,
                                peak(
                                  financial.expenses_by_category.map((row) =>
                                    amountOf(row.amount),
                                  ),
                                ),
                              )}%`,
                              background: color,
                            }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </DashboardPanel>
          </div>

          <DashboardPanel title="Top products">
            <ul className="grid gap-3 md:grid-cols-2">
              {financial.top_product_items.map((item) => {
                const revenue = amountOf(item.revenue);
                return (
                  <li
                    className="rounded-2xl bg-[#f7f9fc] p-4 ring-1 ring-[#e6ebf2]"
                    key={item.product_id}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <span className="min-w-0">
                        <strong className="block text-sm text-[#071833]">
                          {item.name}
                        </strong>
                        <span className="mt-1 block text-xs text-slate-500">
                          {item.quantity_sold} {tx("sold")}
                        </span>
                      </span>
                      <span className="shrink-0 text-right text-xs text-slate-500">
                        <strong className="block text-sm text-[#071833] tabular-nums">
                          {money(item.revenue)}
                        </strong>
                        <span className="tabular-nums">
                          {money(item.gross_profit)} {tx("profit")}
                        </span>
                      </span>
                    </div>
                    <div
                      aria-hidden="true"
                      className="mt-3 h-2.5 overflow-hidden rounded-full bg-white"
                    >
                      <div
                        className="h-full rounded-full bg-[#e6a100]"
                        style={{
                          width: `${barPercent(
                            revenue,
                            peak(
                              financial.top_product_items.map((row) =>
                                amountOf(row.revenue),
                              ),
                            ),
                          )}%`,
                        }}
                      />
                    </div>
                  </li>
                );
              })}
              {!financial.top_product_items.length && (
                <li className="flex flex-col items-center gap-3 rounded-2xl bg-[#f7f9fc] px-6 py-10 text-center text-sm text-slate-500 md:col-span-2">
                  <span
                    aria-hidden="true"
                    className="grid size-14 place-items-center rounded-full bg-white text-[#0866ff] shadow-sm"
                  >
                    <Glyph name="profit" />
                  </span>
                  {tx("No product sales in this period.")}
                </li>
              )}
            </ul>
          </DashboardPanel>
        </section>
      )}
    </div>
  );
}

function amountOf(value: string): number {
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : 0;
}

function peak(values: number[]): number {
  return values.reduce((max, value) => Math.max(max, Math.abs(value)), 0);
}

function barPercent(value: number, max: number): number {
  if (value === 0 || max <= 0) return 0;
  return Math.max(6, (Math.abs(value) / max) * 100);
}

function sourceColor(source: string, amount: number): string {
  if (amount < 0) return "#e23b4a";
  return SOURCE_COLORS[source] ?? "#5c6778";
}

function seriesColor(index: number): string {
  return SERIES[index % SERIES.length] ?? "#0866ff";
}

function amountColor(amount: number, color: string): string {
  if (amount < 0) return "#e23b4a";
  if (amount === 0) return "#d5deea";
  return color;
}

function columnFill(amount: number, tone: MoneyTone): string {
  if (amount === 0) return "#e6ebf2";
  if (amount < 0 || tone === "expense") return "#e23b4a";
  if (tone === "profit") return "#1f9d4d";
  if (tone === "pending") return "#e6a100";
  return "#0866ff";
}

function Metric({
  detail,
  label,
  mark,
  value,
}: {
  detail: string;
  label: string;
  mark: ReactNode;
  value: string;
}) {
  const tx = useTx();
  return (
    <article className={`${CARD} p-6`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-500">{tx(label)}</p>
          <p className="mt-3 text-5xl font-black text-[#071833] tabular-nums">
            {value}
          </p>
          <p className="mt-2 text-xs text-slate-500">{tx(detail)}</p>
        </div>
        <div aria-hidden="true" className="shrink-0">
          {mark}
        </div>
      </div>
    </article>
  );
}

function FinancialMetric({
  amount,
  detail,
  label,
  peak: max,
  tone,
  value,
}: {
  amount: string;
  detail?: string;
  label: string;
  peak: number;
  tone: MoneyTone;
  value: string;
}) {
  const tx = useTx();
  const numeric = amountOf(amount);
  const height = columnHeight(numeric, max);
  return (
    <article className="flex h-full flex-col">
      <div aria-hidden="true" className="flex h-48 items-end justify-center">
        <div
          className="w-10 rounded-t-lg sm:w-14"
          style={{
            height: `${height}%`,
            background: columnFill(numeric, tone),
          }}
        />
      </div>
      <div className="mt-3 min-w-0 text-center">
        <p className="text-xs font-semibold text-slate-500">{tx(label)}</p>
        <p className="mt-1 text-sm font-black text-[#071833] tabular-nums">
          {value}
        </p>
        {detail ? (
          <p className="mt-1 text-xs text-slate-500">{detail}</p>
        ) : null}
      </div>
    </article>
  );
}

function columnHeight(value: number, max: number): number {
  if (value === 0 || max <= 0) return 8;
  return Math.max(16, (Math.abs(value) / max) * 100);
}

function ShareRing({
  children,
  parts,
}: {
  children?: ReactNode;
  parts: { color: string; value: number }[];
}) {
  const slices = parts.filter((part) => part.value > 0);
  const total = slices.reduce((sum, part) => sum + part.value, 0);
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const gap = slices.length > 1 ? 3 : 0;
  let consumed = 0;

  return (
    <div className="relative mx-auto size-56">
      <svg aria-hidden="true" className="size-full" viewBox="0 0 100 100">
        <circle
          cx="50"
          cy="50"
          fill="none"
          r={radius}
          stroke="#e7eef8"
          strokeWidth="16"
        />
        {total > 0 &&
          slices.map((part, index) => {
            const sweep = (part.value / total) * circumference;
            const length = sweep > gap + 1 ? sweep - gap : sweep;
            const offset = consumed;
            consumed += sweep;
            return (
              <circle
                cx="50"
                cy="50"
                fill="none"
                key={`${part.color}-${index}`}
                r={radius}
                stroke={part.color}
                strokeDasharray={`${length} ${circumference - length}`}
                strokeDashoffset={-offset}
                strokeWidth="16"
                transform="rotate(-90 50 50)"
              />
            );
          })}
      </svg>
      <div
        aria-hidden="true"
        className="absolute inset-0 grid place-items-center text-[#0866ff]"
      >
        {children}
      </div>
    </div>
  );
}

function FloorRing({ ratio }: { ratio: number }) {
  const radius = 22;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg
      aria-hidden="true"
      className="size-16 text-[#0866ff]"
      viewBox="0 0 64 64"
    >
      <circle cx="32" cy="32" fill="#e7f3ff" r="30" />
      <circle
        cx="32"
        cy="32"
        fill="none"
        r={radius}
        stroke="currentColor"
        strokeOpacity="0.18"
        strokeWidth="5"
      />
      <circle
        cx="32"
        cy="32"
        fill="none"
        r={radius}
        stroke="currentColor"
        strokeDasharray={`${ratio * circumference} ${circumference}`}
        strokeLinecap="round"
        strokeWidth="5"
        transform="rotate(-90 32 32)"
      />
    </svg>
  );
}

function Glyph({ name }: { name: GlyphName }) {
  const pen = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.8,
  };
  return (
    <svg aria-hidden="true" className="size-6" viewBox="0 0 24 24">
      {name === "members" && (
        <>
          <circle {...pen} cx="9" cy="8" r="3" />
          <circle {...pen} cx="16.5" cy="9" r="2.2" />
          <path {...pen} d="M3.5 19c.6-3 2.7-4.5 5.5-4.5S14 16 14.6 19" />
          <path {...pen} d="M15 14.7c1.5.2 2.8 1.1 3.4 3.3" />
        </>
      )}
      {name === "check" && (
        <>
          <rect {...pen} height="16" rx="3" width="16" x="4" y="4" />
          <path {...pen} d="M8.5 12.5 11 15l4.5-5" />
        </>
      )}
      {name === "join" && (
        <>
          <circle {...pen} cx="10" cy="8" r="3" />
          <path {...pen} d="M4 19c.6-3 2.6-4.5 6-4.5 1.1 0 2.1.2 3 .7" />
          <path {...pen} d="M17 11v6M14 14h6" />
        </>
      )}
      {name === "pulse" && <path {...pen} d="M3 12h4l2.2-5L13 17l2.2-5H21" />}
      {name === "revenue" && (
        <>
          <path {...pen} d="M4 16.5 9 11l3 3 8-8" />
          <path {...pen} d="M14 6h6v6" />
        </>
      )}
      {name === "clock" && (
        <>
          <circle {...pen} cx="12" cy="12" r="8" />
          <path {...pen} d="M12 8v5l3 2" />
        </>
      )}
      {name === "expense" && (
        <>
          <path {...pen} d="M7 3.5h8l3 3V20.5H7z" />
          <path {...pen} d="M15 3.5V7h3.5" />
          <path {...pen} d="M9.5 12h6M9.5 15.5h4" />
        </>
      )}
      {name === "profit" && (
        <>
          <ellipse {...pen} cx="12" cy="7" rx="7" ry="3" />
          <path {...pen} d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7" />
          <path {...pen} d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" />
        </>
      )}
    </svg>
  );
}

function SectionHeading({ id, title }: { id: string; title: string }) {
  const tx = useTx();
  return (
    <h2 className="text-lg font-bold text-slate-900" id={id}>
      {tx(title)}
    </h2>
  );
}

function DashboardPanel({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  const tx = useTx();
  return (
    <section className={`${CARD} p-5 sm:p-7`}>
      <h3 className="mb-6 text-lg font-bold text-[#071833]">{tx(title)}</h3>
      {children}
    </section>
  );
}

function MiniTotal({ label, value }: { label: string; value: string }) {
  const tx = useTx();
  return (
    <div className="rounded-2xl bg-[#f7f9fc] p-3 ring-1 ring-[#e6ebf2]">
      <p className="text-xs font-medium text-slate-500">{tx(label)}</p>
      <p className="mt-1 text-sm font-black text-[#071833] tabular-nums">
        {value}
      </p>
    </div>
  );
}

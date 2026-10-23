"use client";

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

const OPERATIONAL_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
  detail: string;
}[] = [
  {
    key: "active_members",
    label: "Active members",
    detail: "Current active base",
  },
  {
    key: "check_ins",
    label: "Check-ins",
    detail: "Visits in this period",
  },
  {
    key: "new_members",
    label: "New members",
    detail: "Joined in this period",
  },
  {
    key: "currently_checked_in",
    label: "In the gym now",
    detail: "Currently checked in",
  },
];

const MEMBERSHIP_METRICS: {
  key: keyof DashboardData["operational"];
  label: string;
}[] = [
  { key: "unique_attendees", label: "Unique attendees" },
  { key: "expired_memberships", label: "Expired memberships" },
  { key: "expiring_within_3_days", label: "Expiring in 3 days" },
  { key: "expiring_within_7_days", label: "Expiring in 7 days" },
  { key: "expiring_within_30_days", label: "Expiring in 30 days" },
  { key: "pending_approvals", label: "Pending approvals" },
];

export function OwnerDashboard() {
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
        className="rounded-2xl border border-red-200 bg-red-50 p-5"
        role="alert"
      >
        <p className="font-semibold text-red-900">Dashboard unavailable</p>
        <p className="mt-1 text-sm text-red-700">{error}</p>
        <button
          className="button mt-4"
          onClick={() => void load()}
          type="button"
        >
          Try again
        </button>
      </div>
    );
  }
  if (!dashboard) {
    return (
      <div className="grid gap-5" role="status">
        <span className="sr-only">Loading dashboard…</span>
        <div className="h-24 animate-pulse rounded-2xl bg-slate-200" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div
              className="h-36 animate-pulse rounded-2xl bg-slate-200"
              key={item}
            />
          ))}
        </div>
      </div>
    );
  }

  const money = (value: string) => `${value} ${dashboard.currency}`;
  const financial = dashboard.financial;
  const largestRevenue = financial
    ? Math.max(
        ...financial.revenue_sources_ranked.map((item) => Number(item.amount)),
        0,
      )
    : 0;
  return (
    <div className="grid gap-8">
      <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="text-xs font-bold tracking-[0.16em] text-slate-500 uppercase">
              Reporting period
            </p>
            <div
              aria-label="Dashboard range"
              className="mt-3 flex gap-2 overflow-x-auto pb-1"
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
                        ? "shrink-0 rounded-lg bg-slate-800 px-3.5 py-2 text-sm font-semibold text-white shadow-sm"
                        : "shrink-0 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                    }
                    key={item.value}
                    onClick={() => selectPreset(item.value)}
                    type="button"
                  >
                    {item.label}
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
              From
              <input
                className="min-h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800"
                onChange={(event) => setCustomFrom(event.target.value)}
                type="date"
                value={customFrom}
              />
            </label>
            <label className="grid gap-1 text-xs font-semibold text-slate-600">
              To
              <input
                className="min-h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800"
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
              Apply custom range
            </button>
          </form>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-slate-100 pt-4 text-xs text-slate-500">
          <span>
            {dashboard.range.date_from} to {dashboard.range.date_to}
          </span>
          <span>{dashboard.timezone}</span>
          <span>{dashboard.currency}</span>
        </div>
      </section>

      <section aria-labelledby="dashboard-operational" className="grid gap-4">
        <SectionHeading
          eyebrow="At a glance"
          id="dashboard-operational"
          title="Operational totals"
        />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {OPERATIONAL_METRICS.map((metric) => (
            <Metric
              key={metric.key}
              label={metric.label}
              detail={metric.detail}
              value={String(dashboard.operational[metric.key])}
            />
          ))}
        </div>
        <div className="grid overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm sm:grid-cols-2 xl:grid-cols-3">
          {MEMBERSHIP_METRICS.map((metric) => (
            <div
              className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-4 last:border-b-0 sm:[&:nth-last-child(-n+2)]:border-b-0 xl:border-r xl:[&:nth-child(3n)]:border-r-0 xl:[&:nth-last-child(-n+3)]:border-b-0"
              key={metric.key}
            >
              <span className="text-sm font-medium text-slate-600">
                {metric.label}
              </span>
              <strong className="text-xl text-slate-900">
                {dashboard.operational[metric.key]}
              </strong>
            </div>
          ))}
        </div>
      </section>
      {financial === null ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-6 text-sm text-slate-600">
          Financial totals are not included in this response.
        </div>
      ) : (
        <section aria-labelledby="dashboard-financial" className="grid gap-5">
          <SectionHeading
            eyebrow="Business performance"
            id="dashboard-financial"
            title="Financial totals"
          />
          <div className="grid overflow-hidden rounded-2xl bg-slate-800 text-white shadow-lg shadow-slate-900/10 md:grid-cols-2 xl:grid-cols-4">
            <FinancialMetric
              label="Total revenue"
              value={money(financial.total_revenue)}
            />
            <FinancialMetric
              detail={`${financial.pending_payment_member_count} members`}
              label="Pending payments"
              value={money(financial.pending_payment_amount)}
            />
            <FinancialMetric
              label="Total expenses"
              value={money(financial.total_expenses)}
            />
            <FinancialMetric
              accent
              label="Net profit"
              value={money(financial.net_profit)}
            />
          </div>
          <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
            <DashboardPanel title="Revenue sources">
              <ol className="grid gap-4">
                {financial.revenue_sources_ranked.map((item, index) => {
                  const amount = Number(item.amount);
                  const width =
                    largestRevenue > 0
                      ? Math.max(3, (amount / largestRevenue) * 100)
                      : 0;
                  return (
                    <li key={item.source}>
                      <div className="flex items-center justify-between gap-4 text-sm">
                        <span className="font-medium text-slate-700">
                          <span className="mr-2 text-xs text-slate-400">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          {SOURCE_LABELS[item.source] ?? item.source}
                        </span>
                        <strong className="text-slate-900">
                          {money(item.amount)}
                        </strong>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-amber-400"
                          style={{ width: `${width}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ol>
            </DashboardPanel>
            <DashboardPanel title="Expense overview">
              <div className="mb-5 grid grid-cols-2 gap-3">
                <MiniTotal
                  label="Staff salary"
                  value={money(financial.staff_salary_expense)}
                />
                <MiniTotal
                  label="PT commission"
                  value={money(financial.pt_commission_expense)}
                />
              </div>
              <ul className="divide-y divide-slate-100">
                {financial.expenses_by_category.map((item) => (
                  <li
                    className="flex items-center justify-between gap-4 py-3 text-sm first:pt-0 last:pb-0"
                    key={item.system_key}
                  >
                    <span className="text-slate-600">{item.system_key}</span>
                    <strong>{money(item.amount)}</strong>
                  </li>
                ))}
              </ul>
            </DashboardPanel>
          </div>
          <DashboardPanel title="Top products">
            <ul className="grid gap-3 md:grid-cols-2">
              {financial.top_product_items.map((item) => (
                <li
                  className="flex items-center justify-between gap-4 rounded-xl border border-slate-100 bg-slate-50 p-4"
                  key={item.product_id}
                >
                  <span>
                    <strong className="block text-sm text-slate-900">
                      {item.name}
                    </strong>
                    <span className="mt-1 block text-xs text-slate-500">
                      {item.quantity_sold} sold
                    </span>
                  </span>
                  <span className="text-right text-xs text-slate-500">
                    <strong className="block text-sm text-slate-900">
                      {money(item.revenue)}
                    </strong>
                    {money(item.gross_profit)} profit
                  </span>
                </li>
              ))}
              {!financial.top_product_items.length && (
                <li className="rounded-xl bg-slate-50 p-5 text-sm text-slate-500 md:col-span-2">
                  No product sales in this period.
                </li>
              )}
            </ul>
          </DashboardPanel>
        </section>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="relative min-h-36 overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <span
        className="absolute top-0 left-0 h-full w-1 bg-slate-700"
        aria-hidden="true"
      />
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className="mt-3 text-3xl font-black tracking-tight text-slate-900">
        {value}
      </p>
      <p className="mt-2 text-xs text-slate-400">{detail}</p>
    </article>
  );
}

function SectionHeading({
  eyebrow,
  id,
  title,
}: {
  eyebrow: string;
  id: string;
  title: string;
}) {
  return (
    <div>
      <p className="text-xs font-bold tracking-[0.16em] text-amber-600 uppercase">
        {eyebrow}
      </p>
      <h2
        className="mt-1 text-xl font-bold tracking-tight text-slate-900"
        id={id}
      >
        {title}
      </h2>
    </div>
  );
}

function FinancialMetric({
  accent = false,
  detail,
  label,
  value,
}: {
  accent?: boolean;
  detail?: string;
  label: string;
  value: string;
}) {
  return (
    <article
      className={`border-b border-white/10 p-5 last:border-b-0 md:border-r md:[&:nth-child(2n)]:border-r-0 xl:border-b-0 xl:[&:nth-child(2n)]:border-r xl:last:border-r-0 ${
        accent ? "bg-amber-400 text-slate-950" : ""
      }`}
    >
      <p className="text-xs font-bold tracking-wide uppercase opacity-65">
        {label}
      </p>
      <p className="mt-3 text-2xl font-black tracking-tight">{value}</p>
      {detail && <p className="mt-2 text-xs opacity-60">{detail}</p>}
    </article>
  );
}

function DashboardPanel({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <h3 className="mb-5 text-base font-bold text-slate-900">{title}</h3>
      {children}
    </section>
  );
}

function MiniTotal({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-bold text-slate-900">{value}</p>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

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
}[] = [
  { key: "active_members", label: "Active members" },
  { key: "new_members", label: "New members" },
  { key: "expired_memberships", label: "Expired memberships" },
  { key: "check_ins", label: "Check-ins" },
  { key: "unique_attendees", label: "Unique attendees" },
  { key: "currently_checked_in", label: "Currently checked in" },
  { key: "expiring_within_3_days", label: "Expiring within 3 days" },
  { key: "expiring_within_7_days", label: "Expiring within 7 days" },
  { key: "expiring_within_30_days", label: "Expiring within 30 days" },
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
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!dashboard) {
    return (
      <p className="muted" role="status">
        Loading dashboard…
      </p>
    );
  }

  const money = (value: string) => `${value} ${dashboard.currency}`;
  const financial = dashboard.financial;
  return (
    <div className="grid gap-6">
      <div
        aria-label="Dashboard range"
        className="flex flex-wrap gap-2"
        role="group"
      >
        {PRESETS.map((item) => (
          <button
            aria-pressed={query === `range=${item.value}`}
            className={
              preset === item.value && query.startsWith("range=")
                ? "button"
                : "rounded-md border px-4 py-2"
            }
            key={item.value}
            onClick={() => selectPreset(item.value)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          applyCustomRange();
        }}
      >
        <label className="grid gap-1 text-sm">
          From
          <input
            onChange={(event) => setCustomFrom(event.target.value)}
            type="date"
            value={customFrom}
          />
        </label>
        <label className="grid gap-1 text-sm">
          To
          <input
            onChange={(event) => setCustomTo(event.target.value)}
            type="date"
            value={customTo}
          />
        </label>
        <button className="button" type="submit">
          Apply custom range
        </button>
      </form>
      <p className="muted text-sm">
        {dashboard.range.date_from} to {dashboard.range.date_to}.{" "}
        {dashboard.timezone}. {dashboard.currency}.
      </p>
      <section aria-labelledby="dashboard-operational" className="grid gap-4">
        <h2 className="text-lg font-bold" id="dashboard-operational">
          Operational totals
        </h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {OPERATIONAL_METRICS.map((metric) => (
            <Metric
              key={metric.key}
              label={metric.label}
              value={String(dashboard.operational[metric.key])}
            />
          ))}
        </div>
      </section>
      {financial === null ? (
        <p>Financial totals are not included in this response.</p>
      ) : (
        <section aria-labelledby="dashboard-financial" className="grid gap-4">
          <h2 className="text-lg font-bold" id="dashboard-financial">
            Financial totals
          </h2>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Metric
              label="Total revenue"
              value={money(financial.total_revenue)}
            />
            <Metric
              label="Pending payments"
              value={money(financial.pending_payment_amount)}
              detail={`${financial.pending_payment_member_count} members`}
            />
            <Metric
              label="Total expenses"
              value={money(financial.total_expenses)}
            />
            <Metric label="Net profit" value={money(financial.net_profit)} />
            <Metric
              label="Staff salary"
              value={money(financial.staff_salary_expense)}
            />
            <Metric
              label="PT commission"
              value={money(financial.pt_commission_expense)}
            />
          </div>
          <section className="card p-5">
            <h3 className="text-lg font-bold">Revenue sources</h3>
            <ol className="mt-4 grid gap-3">
              {financial.revenue_sources_ranked.map((item, index) => (
                <li
                  className="flex items-center justify-between rounded-lg bg-slate-50 p-4"
                  key={item.source}
                >
                  <span>
                    <strong className="mr-3">{index + 1}</strong>
                    {SOURCE_LABELS[item.source] ?? item.source}
                  </span>
                  <strong>{money(item.amount)}</strong>
                </li>
              ))}
            </ol>
          </section>
          <section className="card p-5">
            <h3 className="text-lg font-bold">Top products</h3>
            <ul className="mt-4 grid gap-3">
              {financial.top_product_items.map((item) => (
                <li
                  className="flex items-center justify-between rounded-lg bg-slate-50 p-4"
                  key={item.product_id}
                >
                  <span>
                    {item.name} · {item.quantity_sold}
                  </span>
                  <strong>
                    {money(item.revenue)} · {money(item.gross_profit)}
                  </strong>
                </li>
              ))}
              {!financial.top_product_items.length && (
                <li className="muted">No product sales in this period.</li>
              )}
            </ul>
          </section>
          <section className="card p-5">
            <h3 className="text-lg font-bold">Expenses by category</h3>
            <ul className="mt-4 grid gap-3">
              {financial.expenses_by_category.map((item) => (
                <li
                  className="flex items-center justify-between rounded-lg bg-slate-50 p-4"
                  key={item.system_key}
                >
                  <span>{item.system_key}</span>
                  <strong>{money(item.amount)}</strong>
                </li>
              ))}
            </ul>
          </section>
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
  detail?: string;
}) {
  return (
    <article className="card p-5">
      <p className="muted text-sm">{label}</p>
      <p className="mt-2 text-2xl font-bold">{value}</p>
      {detail && <p className="muted mt-2 text-sm">{detail}</p>}
    </article>
  );
}

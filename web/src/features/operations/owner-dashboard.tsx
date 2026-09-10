"use client";

import { useCallback, useEffect, useState } from "react";

import { operationGet } from "@/features/operations/client";
import type { DashboardData } from "@/types/api";

const LABELS: Record<string, string> = {
  NEW_MEMBER: "New member",
  RENEWAL: "Member renewal",
  PT: "PT",
  PRODUCT: "Product sales",
  OTHER: "Other",
};

export function OwnerDashboard() {
  const [range, setRange] = useState<"today" | "month">("today");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setDashboard(
        await operationGet<DashboardData>(`dashboard/?range=${range}`),
      );
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load dashboard.",
      );
    }
  }, [range]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!dashboard) return <p className="muted">Loading dashboard…</p>;

  const money = (value: string) => `${value} ${dashboard.currency}`;
  return (
    <div className="grid gap-6">
      <div className="flex gap-2">
        {(["today", "month"] as const).map((value) => (
          <button
            className={
              range === value ? "button" : "rounded-md border px-4 py-2"
            }
            key={value}
            onClick={() => setRange(value)}
            type="button"
          >
            {value === "today" ? "Today" : "This month"}
          </button>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="Income" value={money(dashboard.income)} />
        <Metric
          label="Pending payments"
          value={money(dashboard.pending_payments.amount)}
          detail={`${dashboard.pending_payments.member_count} expired members`}
        />
        <Metric
          label="Expiring members"
          value={String(dashboard.expiring_members)}
          detail="Within 30 days"
        />
        <Metric
          label="Attendance"
          value={
            dashboard.attendance.available
              ? String(dashboard.attendance.today)
              : "Coming next"
          }
          detail="QR/check-in milestone"
        />
      </div>
      <section className="card p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-bold">Revenue sources</h2>
          <span className="muted text-sm">
            {dashboard.pending_approvals} awaiting approval
          </span>
        </div>
        <ol className="mt-4 grid gap-3">
          {dashboard.revenue_sources.map((item, index) => (
            <li
              className="flex items-center justify-between rounded-lg bg-slate-50 p-4"
              key={item.source}
            >
              <span>
                <strong className="mr-3">{index + 1}</strong>
                {LABELS[item.source] ?? item.source}
              </span>
              <strong>{money(item.amount)}</strong>
            </li>
          ))}
          {!dashboard.revenue_sources.length && (
            <li className="muted">No posted revenue in this period.</li>
          )}
        </ol>
      </section>
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

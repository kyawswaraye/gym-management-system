"use client";

import { useEffect, useState } from "react";

import { platformGet } from "@/features/platform/client";
import type { AdminDashboard } from "@/types/api";

const GYM_STATUSES = [
  "total",
  "PENDING",
  "ACTIVE",
  "SUSPENDED",
  "CLOSED",
] as const;
const SUBSCRIPTION_STATUSES = [
  "TRIAL",
  "ACTIVE",
  "PAST_DUE",
  "CANCELLED",
] as const;
const PLAN_STATUSES = ["ACTIVE", "RETIRED"] as const;

function CountTable({
  caption,
  rows,
}: {
  caption: string;
  rows: readonly (readonly [string, number])[];
}) {
  return (
    <table className="w-full text-left text-sm">
      <caption className="mb-3 text-left text-lg font-bold">{caption}</caption>
      <thead>
        <tr className="border-b border-slate-200">
          <th className="py-2 pr-4" scope="col">
            Status
          </th>
          <th className="py-2" scope="col">
            Count
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([status, count]) => (
          <tr key={status} className="border-b border-slate-100">
            <th className="py-3 pr-4 font-medium" scope="row">
              {status}
            </th>
            <td className="py-3">{count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PlatformDashboard({ canView }: { canView: boolean }) {
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(canView);

  useEffect(() => {
    if (!canView) return;
    let active = true;
    void platformGet<AdminDashboard>("admin/dashboard")
      .then((data) => {
        if (active) setDashboard(data);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load the platform dashboard.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [canView]);

  if (!canView) {
    return <p>You do not have access to the platform dashboard.</p>;
  }
  if (loading) return <p className="muted">Loading platform summary.</p>;
  if (error || !dashboard) {
    return (
      <p className="error" role="alert">
        {error ?? "Unable to load the platform dashboard."}
      </p>
    );
  }

  return (
    <div className="grid gap-8 lg:grid-cols-3">
      <CountTable
        caption="Gym statuses"
        rows={GYM_STATUSES.map((status) => [
          status,
          dashboard.gym_statuses[status],
        ])}
      />
      <CountTable
        caption="Gym subscription statuses"
        rows={SUBSCRIPTION_STATUSES.map((status) => [
          status,
          dashboard.gym_subscription_statuses[status],
        ])}
      />
      <CountTable
        caption="Subscription plan statuses"
        rows={PLAN_STATUSES.map((status) => [
          status,
          dashboard.subscription_plan_statuses[status],
        ])}
      />
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { platformGet } from "@/features/platform/client";
import type { AdminDashboard } from "@/types/api";

const GYM_STATUSES = [
  { key: "total", label: "Total" },
  { key: "PENDING", label: "Pending" },
  { key: "ACTIVE", label: "Active" },
  { key: "SUSPENDED", label: "Suspended" },
  { key: "CLOSED", label: "Closed" },
] as const;

const SUBSCRIPTION_STATUSES = [
  { key: "TRIAL", label: "Trial" },
  { key: "ACTIVE", label: "Active" },
  { key: "PAST_DUE", label: "Past due" },
  { key: "CANCELLED", label: "Cancelled" },
] as const;

const PLAN_STATUSES = [
  { key: "ACTIVE", label: "Active" },
  { key: "RETIRED", label: "Retired" },
] as const;

const SHORTCUTS = [
  {
    href: "/super-admin/gyms",
    label: "Gyms",
    detail: "Directory and provisioning",
  },
  {
    href: "/super-admin/subscriptions",
    label: "Subscriptions",
    detail: "Plan price and status",
  },
  {
    href: "/super-admin/audit-logs",
    label: "Audit logs",
    detail: "Platform changes",
  },
] as const;

function attention(label: string, count: number): boolean {
  return count > 0 && (label === "Suspended" || label === "Past due");
}

function Stat({ label, count }: { label: string; count: number }) {
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p
        className={`mt-1 text-2xl font-black tracking-tight ${
          attention(label, count) ? "text-amber-700" : "text-slate-900"
        }`}
      >
        {count}
      </p>
    </div>
  );
}

function Board({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-base font-bold text-slate-900">{title}</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
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
    return (
      <p className="rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center text-sm font-semibold text-slate-700 shadow-sm">
        You do not have access to the platform dashboard.
      </p>
    );
  }
  if (loading) {
    return (
      <div className="grid gap-4" role="status">
        <p className="text-sm font-medium text-slate-500">
          Loading platform summary.
        </p>
        <div className="grid gap-4 lg:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div
              className="h-48 animate-pulse rounded-2xl bg-slate-200"
              key={item}
            />
          ))}
        </div>
      </div>
    );
  }
  if (error || !dashboard) {
    return (
      <p className="error" role="alert">
        {error ?? "Unable to load the platform dashboard."}
      </p>
    );
  }

  return (
    <div className="grid gap-6">
      <nav aria-label="Platform sections" className="grid gap-3 sm:grid-cols-3">
        {SHORTCUTS.map((item) => (
          <Link
            className="rounded-2xl border border-slate-200 bg-white px-4 py-4 shadow-sm transition-colors hover:border-[#0866ff] hover:bg-[#e7f3ff]"
            href={item.href}
            key={item.href}
          >
            <span className="block text-sm font-bold text-slate-900">
              {item.label}
            </span>
            <span className="mt-1 block text-sm text-slate-500">
              {item.detail}
            </span>
          </Link>
        ))}
      </nav>
      <div className="grid gap-4 xl:grid-cols-3">
        <Board title="Gym statuses">
          {GYM_STATUSES.map((status) => (
            <Stat
              count={dashboard.gym_statuses[status.key]}
              key={status.key}
              label={status.label}
            />
          ))}
        </Board>
        <Board title="Gym subscription statuses">
          {SUBSCRIPTION_STATUSES.map((status) => (
            <Stat
              count={dashboard.gym_subscription_statuses[status.key]}
              key={status.key}
              label={status.label}
            />
          ))}
        </Board>
        <Board title="Subscription plan statuses">
          {PLAN_STATUSES.map((status) => (
            <Stat
              count={dashboard.subscription_plan_statuses[status.key]}
              key={status.key}
              label={status.label}
            />
          ))}
        </Board>
      </div>
    </div>
  );
}

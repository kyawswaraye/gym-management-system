"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState, type FormEvent } from "react";

import { FormDialog } from "@/components/form-dialog";
import { platformGet, platformMutation } from "@/features/platform/client";
import {
  buildSubscriptionUpdate,
  compareSubscriptionPlans,
} from "@/features/platform/subscription-policy";
import type {
  PlatformSubscription,
  PlatformSubscriptionDetail,
} from "@/types/api";

const PLAN_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const INTERVAL_LABELS: Record<string, string> = {
  TRIAL: "Trial",
  MONTHLY: "Monthly",
  QUARTERLY: "Quarterly",
  YEARLY: "Yearly",
  LIFETIME: "Lifetime",
};

function intervalLabel(value: string): string {
  return INTERVAL_LABELS[value] ?? value;
}

function toListItem(detail: PlatformSubscriptionDetail): PlatformSubscription {
  return {
    id: detail.id,
    code: detail.code,
    name: detail.name,
    status: detail.status,
    price: detail.price,
    currency: detail.currency,
    billing_interval: detail.billing_interval,
  };
}

export function SubscriptionsManager({ canManage }: { canManage: boolean }) {
  const tx = useTx();
  const [plans, setPlans] = useState<PlatformSubscription[]>([]);
  const [loading, setLoading] = useState(canManage);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [editingCode, setEditingCode] = useState<string | null>(null);

  useEffect(() => {
    if (!canManage) return;
    let active = true;
    void platformGet<PlatformSubscription[]>("admin/subscriptions?status=ALL")
      .then((data) => {
        if (active) setPlans([...data].sort(compareSubscriptionPlans));
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load subscription plans.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [canManage]);

  async function save(
    event: FormEvent<HTMLFormElement>,
    plan: PlatformSubscription,
  ) {
    event.preventDefault();
    setError(null);
    setSavedId(null);
    const planId = plan.id.toLowerCase();
    if (!PLAN_ID.test(planId)) {
      setError(tx("The plan identifier is invalid."));
      return;
    }
    const values = new FormData(event.currentTarget);
    const update = buildSubscriptionUpdate(plan, {
      name: String(values.get("name") ?? ""),
      price: String(values.get("price") ?? ""),
      billingInterval: String(values.get("billing_interval") ?? ""),
      status: String(values.get("status") ?? ""),
    });
    if (!update.ok) {
      setError(update.message);
      return;
    }
    setPendingId(plan.id);
    try {
      const detail = await platformMutation<PlatformSubscriptionDetail>(
        `admin/subscriptions/${planId}`,
        "PATCH",
        { ...update.body },
      );
      const next = toListItem(detail);
      setPlans((current) =>
        current
          .map((item) => (item.id === plan.id ? next : item))
          .sort(compareSubscriptionPlans),
      );
      setEditingCode(null);
      setSavedId(plan.id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to update the subscription plan.",
      );
    } finally {
      setPendingId(null);
    }
  }

  if (!canManage) {
    return (
      <p className="rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center text-sm font-semibold text-slate-700 shadow-sm">
        {" "}
        {tx("You do not have access to subscription plans.")}{" "}
      </p>
    );
  }
  if (loading) {
    return (
      <div className="grid gap-3" role="status">
        <p className="text-sm font-medium text-slate-500">
          {" "}
          {tx("Loading subscription plans.")}{" "}
        </p>
        {[0, 1].map((item) => (
          <div
            className="h-24 animate-pulse rounded-2xl bg-slate-200"
            key={item}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      {error && (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      )}
      {!error && plans.length === 0 && (
        <p className="rounded-2xl border border-slate-200 bg-white px-6 py-14 text-center text-sm font-semibold text-slate-700 shadow-sm">
          {" "}
          {tx("No subscription plans found.")}{" "}
        </p>
      )}
      {plans.length > 0 ? (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          {savedId ? (
            <p
              className="border-b border-slate-100 px-5 py-3 text-sm font-medium text-emerald-700"
              role="status"
            >
              {plans.find((plan) => plan.id === savedId)?.code}{" "}
              {tx("was updated.")}{" "}
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  <th className="px-5 py-3" scope="col">
                    {" "}
                    {tx("Plan")}{" "}
                  </th>
                  <th className="px-5 py-3" scope="col">
                    {" "}
                    {tx("Interval")}{" "}
                  </th>
                  <th className="px-5 py-3" scope="col">
                    {" "}
                    {tx("Price")}{" "}
                  </th>
                  <th className="px-5 py-3" scope="col">
                    {" "}
                    {tx("Currency")}{" "}
                  </th>
                  <th className="px-5 py-3" scope="col">
                    {" "}
                    {tx("Status")}{" "}
                  </th>
                  <th className="px-5 py-3" scope="col">
                    <span className="sr-only">{tx("Edit")}</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plans.map((plan) => (
                  <tr
                    aria-label={tx("{code} plan", { code: plan.code })}
                    className="bg-white hover:bg-slate-50"
                    key={`${plan.id}:${plan.name}:${plan.status}:${plan.price}:${plan.billing_interval}`}
                    role="article"
                  >
                    <td className="px-5 py-4">
                      <p className="font-bold text-slate-900">{plan.name}</p>
                      <p className="mt-0.5 text-xs font-medium text-slate-500">
                        {plan.code}
                      </p>
                    </td>
                    <td className="px-5 py-4 text-slate-700">
                      {intervalLabel(plan.billing_interval)}
                    </td>
                    <td className="px-5 py-4 font-semibold text-slate-800 tabular-nums">
                      {plan.price}
                    </td>
                    <td className="px-5 py-4 font-medium text-slate-700">
                      {tx(plan.currency)}
                    </td>
                    <td className="px-5 py-4">
                      <span
                        aria-label={tx("Current status for {code}", {
                          code: plan.code,
                        })}
                        className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${
                          plan.status === "ACTIVE"
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-slate-200 text-slate-600"
                        }`}
                      >
                        {tx(plan.status)}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-right">
                      <button
                        aria-label={tx("Edit {name}", { name: plan.code })}
                        className="button button-edit button-sm"
                        onClick={() => setEditingCode(plan.code)}
                        type="button"
                      >
                        {" "}
                        {tx("Edit")}{" "}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {plans.map((plan) =>
        editingCode === plan.code ? (
          <FormDialog
            key={plan.id}
            onClose={() => {
              if (pendingId === null) setEditingCode(null);
            }}
            onSubmit={(event) => void save(event, plan)}
            pending={pendingId === plan.id}
            title={tx("Edit {name}", { name: plan.code })}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <label className="field">
                <span>
                  {tx("Name for")} {plan.code}
                </span>
                <input
                  className="input"
                  name="name"
                  defaultValue={plan.name}
                  maxLength={100}
                  required
                  disabled={pendingId !== null}
                />
              </label>
              <label className="field">
                <span>
                  {tx("Price for")} {plan.code}
                </span>
                <input
                  className="input"
                  name="price"
                  inputMode="decimal"
                  defaultValue={plan.price}
                  required
                  disabled={pendingId !== null}
                />
              </label>
              <label className="field">
                <span>
                  {tx("Billing interval for")} {plan.code}
                </span>
                <input
                  className="input"
                  name="billing_interval"
                  defaultValue={plan.billing_interval}
                  maxLength={20}
                  required
                  disabled={pendingId !== null}
                />
              </label>
              <label className="field">
                <span>
                  {tx("Status for")} {plan.code}
                </span>
                <select
                  className="input"
                  name="status"
                  defaultValue={plan.status}
                  disabled={pendingId !== null}
                >
                  <option value="ACTIVE">{tx("ACTIVE")}</option>
                  <option value="RETIRED">{tx("RETIRED")}</option>
                </select>
              </label>
              <button
                className="button button-edit md:col-span-2"
                type="submit"
                disabled={pendingId !== null}
              >
                {pendingId === plan.id
                  ? tx("Saving…")
                  : tx("Save {name}", { name: plan.code })}
              </button>
            </div>
          </FormDialog>
        ) : null,
      )}
    </div>
  );
}

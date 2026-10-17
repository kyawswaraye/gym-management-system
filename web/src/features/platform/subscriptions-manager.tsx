"use client";

import { useEffect, useState, type FormEvent } from "react";

import { platformGet, platformMutation } from "@/features/platform/client";
import { buildSubscriptionUpdate } from "@/features/platform/subscription-policy";
import type {
  PlatformSubscription,
  PlatformSubscriptionDetail,
} from "@/types/api";

const PLAN_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

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
  const [plans, setPlans] = useState<PlatformSubscription[]>([]);
  const [loading, setLoading] = useState(canManage);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);

  useEffect(() => {
    if (!canManage) return;
    let active = true;
    void platformGet<PlatformSubscription[]>("admin/subscriptions?status=ALL")
      .then((data) => {
        if (active) setPlans(data);
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
      setError("The plan identifier is invalid.");
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
        current.map((item) => (item.id === plan.id ? next : item)),
      );
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
    return <p>You do not have access to subscription plans.</p>;
  }
  if (loading) return <p className="muted">Loading subscription plans.</p>;

  return (
    <div className="grid gap-4">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!error && plans.length === 0 && (
        <p className="muted">No subscription plans found.</p>
      )}
      {plans.map((plan) => (
        <article
          key={`${plan.id}:${plan.name}:${plan.status}:${plan.price}:${plan.billing_interval}`}
          className="rounded-lg border border-slate-200 bg-white p-4"
          aria-label={`${plan.code} plan`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-bold">{plan.code}</h2>
            <p>
              Current status:{" "}
              <span aria-label={`Current status for ${plan.code}`}>
                {plan.status}
              </span>
            </p>
          </div>
          <p className="muted mt-1 text-sm">Currency {plan.currency}</p>
          <form
            className="mt-4 grid gap-3 md:grid-cols-2"
            onSubmit={(event) => void save(event, plan)}
          >
            <label className="field">
              <span>Name for {plan.code}</span>
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
              <span>Price for {plan.code}</span>
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
              <span>Billing interval for {plan.code}</span>
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
              <span>Status for {plan.code}</span>
              <select
                className="input"
                name="status"
                defaultValue={plan.status}
                disabled={pendingId !== null}
              >
                <option value="ACTIVE">ACTIVE</option>
                <option value="RETIRED">RETIRED</option>
              </select>
            </label>
            <button
              className="button md:col-span-2"
              type="submit"
              disabled={pendingId !== null}
            >
              {pendingId === plan.id ? "Saving…" : `Save ${plan.code}`}
            </button>
          </form>
          {savedId === plan.id && (
            <p
              className="mt-3 text-sm font-medium text-emerald-700"
              role="status"
            >
              {plan.code} was updated.
            </p>
          )}
        </article>
      ))}
    </div>
  );
}

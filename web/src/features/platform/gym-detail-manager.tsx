"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  platformGet,
  platformGetCollection,
  platformMutation,
} from "@/features/platform/client";
import type {
  PlatformAuditItem,
  PlatformGymDetail,
  PlatformSubscription,
} from "@/types/api";

type Capabilities = {
  canUpdate: boolean;
  canManageSubscriptions: boolean;
  canActivate: boolean;
  canSuspend: boolean;
  canViewAudit: boolean;
  canCreateOwner: boolean;
  mfaRecent: boolean;
};

function dateTimeInput(value: string | null): string {
  return value ? value.slice(0, 16) : "";
}

function dateTimeValue(value: FormDataEntryValue | null): string | null {
  const text = String(value ?? "").trim();
  return text ? new Date(text).toISOString() : null;
}

export function GymDetailManager({
  gymId,
  capabilities,
}: {
  gymId: string;
  capabilities: Capabilities;
}) {
  const [gym, setGym] = useState<PlatformGymDetail | null>(null);
  const [plans, setPlans] = useState<PlatformSubscription[]>([]);
  const [audits, setAudits] = useState<PlatformAuditItem[]>([]);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const [detail, subscriptions, auditCollection] = await Promise.all([
      platformGet<PlatformGymDetail>(`admin/gyms/${gymId}`),
      capabilities.canManageSubscriptions
        ? platformGet<PlatformSubscription[]>("admin/subscriptions")
        : Promise.resolve([]),
      capabilities.canViewAudit
        ? platformGetCollection<PlatformAuditItem>(
            `admin/audit-logs?target_gym_id=${encodeURIComponent(gymId)}`,
          )
        : Promise.resolve(null),
    ]);
    return {
      detail,
      subscriptions,
      audits: auditCollection?.data ?? [],
    };
  }, [capabilities.canManageSubscriptions, capabilities.canViewAudit, gymId]);

  useEffect(() => {
    let active = true;
    void fetchData()
      .then((data) => {
        if (active) {
          setGym(data.detail);
          setPlans(data.subscriptions);
          setAudits(data.audits);
        }
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "Unable to load gym.",
          );
      });
    return () => {
      active = false;
    };
  }, [fetchData]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const values = new FormData(event.currentTarget);
    const request: Record<string, unknown> = {};
    if (capabilities.canUpdate) {
      for (const field of [
        "name",
        "legal_name",
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
        "timezone",
        "currency",
      ]) {
        request[field] = String(values.get(field) ?? "").trim();
      }
    }
    if (capabilities.canManageSubscriptions) {
      request.subscription_plan_id = String(
        values.get("subscription_plan_id") ?? "",
      );
      request.subscription_status = String(
        values.get("subscription_status") ?? "",
      );
      request.subscription_starts_at = dateTimeValue(
        values.get("subscription_starts_at"),
      );
      request.subscription_ends_at = dateTimeValue(
        values.get("subscription_ends_at"),
      );
    }
    setPending(true);
    try {
      const updated = await platformMutation<PlatformGymDetail>(
        `admin/gyms/${gymId}`,
        "PATCH",
        request,
      );
      setGym(updated);
      setSuccess("Gym settings saved.");
      const data = await fetchData();
      setGym(data.detail);
      setPlans(data.subscriptions);
      setAudits(data.audits);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to update gym.",
      );
    } finally {
      setPending(false);
    }
  }

  async function transition(action: "activate" | "suspend") {
    setError(null);
    setSuccess(null);
    if (reason.trim().length < 3) {
      setError("Enter a reason of at least 3 characters.");
      return;
    }
    if (!confirmed) {
      setError("Confirm the lifecycle change before continuing.");
      return;
    }
    setPending(true);
    try {
      const updated = await platformMutation<PlatformGymDetail>(
        `admin/gyms/${gymId}/${action}`,
        "POST",
        { reason: reason.trim() },
      );
      setGym(updated);
      setReason("");
      setConfirmed(false);
      setSuccess(`Gym ${action} request completed.`);
      const data = await fetchData();
      setGym(data.detail);
      setPlans(data.subscriptions);
      setAudits(data.audits);
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : `Unable to ${action} gym.`,
      );
    } finally {
      setPending(false);
    }
  }

  async function inviteOwner(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const form = event.currentTarget;
    const values = new FormData(form);
    setPending(true);
    try {
      await platformMutation(`admin/gyms/${gymId}/owners`, "POST", {
        email: String(values.get("owner_email") ?? "").trim(),
        first_name: String(values.get("owner_first_name") ?? "").trim(),
        last_name: String(values.get("owner_last_name") ?? "").trim(),
        phone: String(values.get("owner_phone") ?? "").trim(),
        send_invitation: true,
      });
      setSuccess(
        "Owner invitation request accepted. A live pending invitation is not resent. A new link is possible after expiry for a passwordless active Owner.",
      );
      form.reset();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Unable to invite Owner.",
      );
    } finally {
      setPending(false);
    }
  }

  if (!gym && !error) return <p className="muted">Loading gym…</p>;

  return (
    <div className="grid gap-6">
      <Link
        className="text-sm text-blue-700 hover:underline"
        href="/super-admin/gyms"
      >
        ← Back to gyms
      </Link>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="text-sm font-medium text-emerald-700" role="status">
          {success}
        </p>
      )}
      {gym && (
        <>
          <section className="card">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-2xl font-bold">{gym.name}</h2>
                <p className="muted mt-1">
                  {gym.code} · {gym.status}
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-medium">
                {gym.subscription_status}
              </span>
            </div>
          </section>

          {(capabilities.canUpdate || capabilities.canManageSubscriptions) && (
            <section className="card" aria-labelledby="gym-settings-title">
              <h2 id="gym-settings-title" className="text-xl font-bold">
                Gym settings
              </h2>
              <form className="mt-5 grid gap-4 md:grid-cols-2" onSubmit={save}>
                {capabilities.canUpdate && (
                  <>
                    {[
                      ["name", "Name"],
                      ["legal_name", "Legal name"],
                      ["email", "Email"],
                      ["phone", "Phone"],
                      ["address_line1", "Address line 1"],
                      ["address_line2", "Address line 2"],
                      ["city", "City"],
                      ["region", "Region"],
                      ["postal_code", "Postal code"],
                      ["country_code", "Country code"],
                      ["timezone", "Timezone"],
                      ["currency", "Currency"],
                    ].map(([field, label]) => (
                      <label className="field" key={field}>
                        <span>{label}</span>
                        <input
                          className="input"
                          name={field}
                          defaultValue={String(
                            gym[field as keyof PlatformGymDetail] ?? "",
                          )}
                          disabled={pending}
                        />
                      </label>
                    ))}
                  </>
                )}
                {capabilities.canManageSubscriptions && (
                  <>
                    <label className="field">
                      <span>Subscription plan</span>
                      <select
                        className="input"
                        name="subscription_plan_id"
                        defaultValue={gym.subscription_plan_id ?? ""}
                        disabled={pending}
                      >
                        {plans.map((plan) => (
                          <option key={plan.id} value={plan.id}>
                            {plan.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Subscription status</span>
                      <select
                        className="input"
                        name="subscription_status"
                        defaultValue={gym.subscription_status}
                        disabled={pending}
                      >
                        {["TRIAL", "ACTIVE", "PAST_DUE", "CANCELLED"].map(
                          (status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label className="field">
                      <span>Subscription starts</span>
                      <input
                        className="input"
                        name="subscription_starts_at"
                        type="datetime-local"
                        defaultValue={dateTimeInput(
                          gym.subscription_starts_at ?? null,
                        )}
                        disabled={pending}
                      />
                    </label>
                    <label className="field">
                      <span>Subscription ends</span>
                      <input
                        className="input"
                        name="subscription_ends_at"
                        type="datetime-local"
                        defaultValue={dateTimeInput(
                          gym.subscription_ends_at ?? null,
                        )}
                        disabled={pending}
                      />
                    </label>
                  </>
                )}
                <button className="button md:col-span-2" disabled={pending}>
                  {pending ? "Saving…" : "Save changes"}
                </button>
              </form>
            </section>
          )}

          {(capabilities.canActivate || capabilities.canSuspend) && (
            <section className="card" aria-labelledby="lifecycle-title">
              <h2 id="lifecycle-title" className="text-xl font-bold">
                Lifecycle
              </h2>
              <p className="muted mt-2 text-sm">
                Suspending immediately revokes tenant staff sessions.
              </p>
              <label className="field mt-4">
                <span>Reason *</span>
                <textarea
                  className="input min-h-24"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  maxLength={500}
                  disabled={pending}
                />
              </label>
              <label className="mt-3 flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={pending}
                />
                I confirm this lifecycle change.
              </label>
              <div className="mt-4 flex flex-wrap gap-3">
                {capabilities.canActivate && (
                  <button
                    className="button"
                    type="button"
                    disabled={pending}
                    onClick={() => void transition("activate")}
                  >
                    Activate gym
                  </button>
                )}
                {capabilities.canSuspend && (
                  <button
                    className="button"
                    type="button"
                    disabled={pending}
                    onClick={() => void transition("suspend")}
                  >
                    Suspend gym
                  </button>
                )}
              </div>
            </section>
          )}

          {capabilities.canCreateOwner && (
            <section className="card" aria-labelledby="add-owner-title">
              <h2 id="add-owner-title" className="text-xl font-bold">
                Add Owner
              </h2>
              <p className="muted mt-2 text-sm">
                Email delivery is required. A live pending invitation is not
                resent. A new link is possible after expiry for a passwordless
                active Owner.
              </p>
              {!capabilities.mfaRecent ? (
                <p className="mt-3 text-sm">
                  Recent MFA verification is required.{" "}
                  <Link
                    className="text-blue-700 hover:underline"
                    href="/mfa-setup"
                  >
                    Set up or verify MFA
                  </Link>
                </p>
              ) : (
                <form
                  className="mt-4 grid gap-3 md:grid-cols-2"
                  onSubmit={inviteOwner}
                >
                  <div className="field">
                    <label>
                      <span>Email</span>
                      <input
                        className="input"
                        name="owner_email"
                        type="email"
                        required
                        disabled={pending}
                        aria-describedby="platform-owner-email-delivery"
                      />
                    </label>
                    <p
                      id="platform-owner-email-delivery"
                      className="muted text-sm"
                    >
                      Required. The setup link is emailed to this address.
                    </p>
                  </div>
                  <label className="field">
                    <span>Phone</span>
                    <input
                      className="input"
                      name="owner_phone"
                      disabled={pending}
                    />
                  </label>
                  <label className="field">
                    <span>First name</span>
                    <input
                      className="input"
                      name="owner_first_name"
                      required
                      disabled={pending}
                    />
                  </label>
                  <label className="field">
                    <span>Last name</span>
                    <input
                      className="input"
                      name="owner_last_name"
                      required
                      disabled={pending}
                    />
                  </label>
                  <button
                    className="button md:col-span-2"
                    type="submit"
                    disabled={pending}
                  >
                    {pending ? "Sending…" : "Send Owner invitation"}
                  </button>
                </form>
              )}
            </section>
          )}

          {capabilities.canViewAudit && (
            <section className="card" aria-labelledby="audit-title">
              <h2 id="audit-title" className="text-xl font-bold">
                Audit timeline
              </h2>
              <ol className="mt-4 grid gap-3">
                {audits.map((audit) => (
                  <li
                    className="rounded-lg border border-slate-200 p-3 text-sm"
                    key={audit.id}
                  >
                    <div className="flex flex-wrap justify-between gap-2">
                      <strong>{audit.action}</strong>
                      <time dateTime={audit.occurred_at}>
                        {new Date(audit.occurred_at).toLocaleString()}
                      </time>
                    </div>
                    <p className="muted mt-1">
                      {audit.actor_email} · {audit.outcome}
                    </p>
                  </li>
                ))}
                {audits.length === 0 && (
                  <li className="muted text-sm">No audit events found.</li>
                )}
              </ol>
            </section>
          )}
        </>
      )}
    </div>
  );
}

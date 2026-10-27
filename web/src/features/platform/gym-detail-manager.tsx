"use client";
import { useLocale, useTx } from "@/i18n/use-tx";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { DialogFrame, FormDialog } from "@/components/form-dialog";
import {
  platformGet,
  platformGetCollection,
  platformMutation,
} from "@/features/platform/client";
import { compareSubscriptionPlans } from "@/features/platform/subscription-policy";
import {
  myanmarPlaceText,
  MyanmarRegionCityFields,
} from "@/features/operations/myanmar-place-fields";
import { formatAppDateTime } from "@/lib/date";
import type {
  PlatformAuditItem,
  PlatformGymDetail,
  PlatformSubscription,
} from "@/types/api";

const panelClass =
  "w-full rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6";

function Fact({ label, value }: { label: string; value: string }) {
  const tx = useTx();
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <dt className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
        {tx(label)}
      </dt>
      <dd className="mt-1 text-sm font-semibold break-words text-slate-900">
        {value || "—"}
      </dd>
    </div>
  );
}

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
  const tx = useTx();
  const locale = useLocale();
  const [gym, setGym] = useState<PlatformGymDetail | null>(null);
  const [plans, setPlans] = useState<PlatformSubscription[]>([]);
  const [audits, setAudits] = useState<PlatformAuditItem[]>([]);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [editingSettings, setEditingSettings] = useState(false);
  const [address, setAddress] = useState({ region: "", city: "" });
  const [lifecycleOpen, setLifecycleOpen] = useState(false);
  const [invitingOwner, setInvitingOwner] = useState(false);
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
          setPlans([...data.subscriptions].sort(compareSubscriptionPlans));
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
      request.legal_name = request.name;
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
      setEditingSettings(false);
      setSuccess("Gym settings saved.");
      const data = await fetchData();
      setGym(data.detail);
      setPlans([...data.subscriptions].sort(compareSubscriptionPlans));
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
      setError(tx("Enter a reason of at least 3 characters."));
      return;
    }
    if (!confirmed) {
      setError(tx("Confirm the lifecycle change before continuing."));
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
      setLifecycleOpen(false);
      setSuccess(tx("Gym {action} request completed.", { action: tx(action) }));
      const data = await fetchData();
      setGym(data.detail);
      setPlans([...data.subscriptions].sort(compareSubscriptionPlans));
      setAudits(data.audits);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : tx("Unable to {action} gym.", { action }),
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
      setInvitingOwner(false);
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

  if (!gym && !error) return <p className="muted">{tx("Loading gym…")}</p>;

  return (
    <div className="grid gap-6">
      <Link
        className="w-fit text-sm font-semibold text-[#0866ff] hover:underline"
        href="/super-admin/gyms"
      >
        {" "}
        {tx("← Back to gyms")}{" "}
      </Link>
      {error && (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      )}
      {success && (
        <p className="text-sm font-medium text-emerald-700" role="status">
          {tx(success)}
        </p>
      )}
      {gym && (
        <>
          <section className={panelClass}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-bold tracking-[0.16em] text-[#0866ff] uppercase">
                  {" "}
                  {tx("Gyms")}{" "}
                </p>
                <h2
                  className="mt-2 text-3xl font-black tracking-tight text-slate-900"
                  id="platform-gym-title"
                >
                  {gym.name}
                </h2>
                <p className="muted mt-2">
                  {gym.code} · {tx(gym.status)}
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-semibold text-slate-700">
                {tx(gym.subscription_status)}
              </span>
            </div>
            <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Plan" value={gym.subscription_plan_name ?? ""} />
              <Fact label="Timezone" value={gym.timezone} />
              <Fact label="Currency" value={gym.currency} />
              <Fact
                label="Region"
                value={myanmarPlaceText(gym.region, locale)}
              />
              <Fact label="City" value={myanmarPlaceText(gym.city, locale)} />
            </dl>
          </section>

          {(capabilities.canUpdate || capabilities.canManageSubscriptions) && (
            <>
              <section
                className={panelClass}
                aria-labelledby="gym-settings-title"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 id="gym-settings-title" className="text-xl font-bold">
                    {" "}
                    {tx("Gym settings")}{" "}
                  </h2>
                  <button
                    className="button button-edit"
                    onClick={() => {
                      setAddress({ region: gym.region, city: gym.city });
                      setEditingSettings(true);
                    }}
                    type="button"
                  >
                    {" "}
                    {tx("Edit gym settings")}{" "}
                  </button>
                </div>
              </section>
              {editingSettings ? (
                <FormDialog
                  onClose={() => {
                    if (!pending) setEditingSettings(false);
                  }}
                  onSubmit={save}
                  pending={pending}
                  title={tx("Gym settings")}
                  wide
                >
                  <div className="grid gap-4 md:grid-cols-2">
                    {capabilities.canUpdate && (
                      <>
                        {(
                          [
                            ["name", "Name"],
                            ["email", "Email"],
                            ["phone", "Phone"],
                            ["address_line1", "Address line 1"],
                            ["address_line2", "Address line 2"],
                          ] as const
                        ).map(([field, label]) => (
                          <label className="field" key={field}>
                            <span>{tx(label)}</span>
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
                        <MyanmarRegionCityFields
                          city={address.city}
                          disabled={pending}
                          onChange={setAddress}
                          region={address.region}
                        />
                        {(
                          [
                            ["postal_code", "Postal code"],
                            ["country_code", "Country code"],
                            ["timezone", "Timezone"],
                          ] as const
                        ).map(([field, label]) => (
                          <label className="field" key={field}>
                            <span>{tx(label)}</span>
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
                        <label className="field">
                          <span>{tx("Currency")}</span>
                          <select
                            className="input"
                            name="currency"
                            defaultValue={
                              gym.currency === "MMK" ? "MMK" : "THB"
                            }
                            disabled={pending}
                          >
                            <option value="THB">{tx("THB")}</option>
                            <option value="MMK">{tx("MMK")}</option>
                          </select>
                        </label>
                      </>
                    )}
                    {capabilities.canManageSubscriptions && (
                      <>
                        <label className="field">
                          <span>{tx("Subscription plan")}</span>
                          <select
                            className="input"
                            name="subscription_plan_id"
                            defaultValue={gym.subscription_plan_id ?? ""}
                            disabled={pending}
                          >
                            {plans.map((plan) => (
                              <option key={plan.id} value={plan.id}>
                                {plan.name} ({tx(plan.currency)})
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field">
                          <span>{tx("Subscription status")}</span>
                          <select
                            className="input"
                            name="subscription_status"
                            defaultValue={gym.subscription_status}
                            disabled={pending}
                          >
                            {["TRIAL", "ACTIVE", "PAST_DUE", "CANCELLED"].map(
                              (status) => (
                                <option key={status} value={status}>
                                  {tx(status)}
                                </option>
                              ),
                            )}
                          </select>
                        </label>
                        <label className="field">
                          <span>{tx("Subscription starts")}</span>
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
                          <span>{tx("Subscription ends")}</span>
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
                    <button
                      className="button button-edit md:col-span-2"
                      disabled={pending}
                    >
                      {pending ? tx("Saving…") : tx("Save changes")}
                    </button>
                  </div>
                </FormDialog>
              ) : null}
            </>
          )}

          {(capabilities.canActivate || capabilities.canSuspend) && (
            <>
              <section className={panelClass} aria-labelledby="lifecycle-title">
                <h2 id="lifecycle-title" className="text-xl font-bold">
                  {" "}
                  {tx("Lifecycle")}{" "}
                </h2>
                <p className="muted mt-2 text-sm">
                  {" "}
                  {tx(
                    "Suspending immediately revokes tenant staff sessions.",
                  )}{" "}
                </p>
                <button
                  className="button button-warn mt-4"
                  onClick={() => setLifecycleOpen(true)}
                  type="button"
                >
                  {" "}
                  {tx("Change lifecycle")}{" "}
                </button>
              </section>
              {lifecycleOpen ? (
                <DialogFrame
                  description="Suspending immediately revokes tenant staff sessions."
                  onClose={() => {
                    if (!pending) setLifecycleOpen(false);
                  }}
                  pending={pending}
                  title={tx("Lifecycle")}
                >
                  <label className="field">
                    <span>{tx("Reason *")}</span>
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
                    />{" "}
                    {tx("I confirm this lifecycle change.")}{" "}
                  </label>
                  <div className="mt-4 flex flex-wrap gap-3">
                    {capabilities.canActivate && (
                      <button
                        className="button button-success"
                        type="button"
                        disabled={pending}
                        onClick={() => void transition("activate")}
                      >
                        {" "}
                        {tx("Activate gym")}{" "}
                      </button>
                    )}
                    {capabilities.canSuspend && (
                      <button
                        className="button button-warn"
                        type="button"
                        disabled={pending}
                        onClick={() => void transition("suspend")}
                      >
                        {" "}
                        {tx("Suspend gym")}{" "}
                      </button>
                    )}
                  </div>
                </DialogFrame>
              ) : null}
            </>
          )}

          {capabilities.canCreateOwner && (
            <section className={panelClass} aria-labelledby="add-owner-title">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="add-owner-title" className="text-xl font-bold">
                  {" "}
                  {tx("Add Owner")}{" "}
                </h2>
                {capabilities.mfaRecent ? (
                  <button
                    className="button button-create"
                    onClick={() => setInvitingOwner(true)}
                    type="button"
                  >
                    {" "}
                    {tx("Add Owner")}{" "}
                  </button>
                ) : null}
              </div>
              <p className="muted mt-2 text-sm">
                {" "}
                {tx(
                  "Email delivery is required. A live pending invitation is not resent. A new link is possible after expiry for a passwordless active Owner.",
                )}{" "}
              </p>
              {!capabilities.mfaRecent ? (
                <p className="mt-3 text-sm">
                  {" "}
                  {tx("Recent MFA verification is required.")}{" "}
                  <Link
                    className="text-blue-700 hover:underline"
                    href="/mfa-setup"
                  >
                    {" "}
                    {tx("Set up or verify MFA")}{" "}
                  </Link>
                </p>
              ) : invitingOwner ? (
                <FormDialog
                  onClose={() => {
                    if (!pending) setInvitingOwner(false);
                  }}
                  onSubmit={inviteOwner}
                  pending={pending}
                  title={tx("Add Owner")}
                >
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="field">
                      <label>
                        <span>{tx("Email")}</span>
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
                        {" "}
                        {tx(
                          "Required. The setup link is emailed to this address.",
                        )}{" "}
                      </p>
                    </div>
                    <label className="field">
                      <span>{tx("Phone")}</span>
                      <input
                        className="input"
                        name="owner_phone"
                        disabled={pending}
                      />
                    </label>
                    <label className="field">
                      <span>{tx("First name")}</span>
                      <input
                        className="input"
                        name="owner_first_name"
                        required
                        disabled={pending}
                      />
                    </label>
                    <label className="field">
                      <span>{tx("Last name")}</span>
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
                      {pending ? tx("Sending…") : tx("Send Owner invitation")}
                    </button>
                  </div>
                </FormDialog>
              ) : null}
            </section>
          )}

          {capabilities.canViewAudit && (
            <section className={panelClass} aria-labelledby="audit-title">
              <h2 id="audit-title" className="text-xl font-bold">
                {" "}
                {tx("Audit timeline")}{" "}
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
                        {formatAppDateTime(audit.occurred_at)}
                      </time>
                    </div>
                    <p className="muted mt-1">
                      {audit.actor_email} · {audit.outcome}
                    </p>
                  </li>
                ))}
                {audits.length === 0 && (
                  <li className="muted text-sm">
                    {tx("No audit events found.")}
                  </li>
                )}
              </ol>
            </section>
          )}
        </>
      )}
    </div>
  );
}

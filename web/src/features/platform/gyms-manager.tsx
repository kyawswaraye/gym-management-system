"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { FormDialog } from "@/components/form-dialog";
import { MyanmarRegionCityFields } from "@/features/operations/myanmar-place-fields";
import {
  platformGet,
  platformGetCollection,
  provisionGym,
} from "@/features/platform/client";
import { compareSubscriptionPlans } from "@/features/platform/subscription-policy";
import type {
  GymProvisionRequest,
  PlatformGym,
  PlatformSubscription,
} from "@/types/api";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "PENDING", label: "Pending" },
  { value: "ACTIVE", label: "Active" },
  { value: "SUSPENDED", label: "Suspended" },
  { value: "CLOSED", label: "Closed" },
] as const;

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-emerald-100 text-emerald-700",
  PENDING: "bg-sky-100 text-sky-800",
  TRIAL: "bg-sky-100 text-sky-800",
  SUSPENDED: "bg-amber-100 text-amber-800",
  PAST_DUE: "bg-amber-100 text-amber-800",
  CLOSED: "bg-slate-200 text-slate-600",
  CANCELLED: "bg-red-100 text-red-700",
};

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Pending",
  ACTIVE: "Active",
  SUSPENDED: "Suspended",
  CLOSED: "Closed",
  TRIAL: "Trial",
  PAST_DUE: "Past due",
  CANCELLED: "Cancelled",
};

function StatusBadge({ value }: { value: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${STATUS_TONE[value] ?? "bg-slate-100 text-slate-600"}`}
    >
      {tx(STATUS_LABEL[value] ?? value)}
    </span>
  );
}

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

export function GymsManager({ canProvision }: { canProvision: boolean }) {
  const tx = useTx();
  const [gyms, setGyms] = useState<PlatformGym[]>([]);
  const [subscriptions, setSubscriptions] = useState<PlatformSubscription[]>(
    [],
  );
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const [address, setAddress] = useState({ region: "", city: "" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const idempotencyKey = useRef<string | null>(null);

  const loadGyms = useCallback(
    async (query = "", after?: string, gymStatus = "") => {
      const parameters = new URLSearchParams();
      if (query.trim()) parameters.set("search", query.trim());
      if (gymStatus) parameters.set("status", gymStatus);
      if (after) parameters.set("after", after);
      const suffix = parameters.size ? `?${parameters.toString()}` : "";
      const body = await platformGetCollection<PlatformGym>(
        `admin/gyms${suffix}`,
      );
      setGyms((current) => (after ? [...current, ...body.data] : body.data));
      setNextAfter(body.meta.next_after ?? null);
    },
    [],
  );

  useEffect(() => {
    let active = true;
    void Promise.all([
      platformGetCollection<PlatformGym>("admin/gyms"),
      canProvision
        ? platformGet<PlatformSubscription[]>(
            "admin/subscriptions?status=ACTIVE",
          )
        : Promise.resolve([]),
    ])
      .then(([gymCollection, plans]) => {
        if (active) {
          setGyms(gymCollection.data);
          setNextAfter(gymCollection.meta.next_after ?? null);
          setSubscriptions(
            plans
              .filter((plan) => plan.status === "ACTIVE")
              .sort(compareSubscriptionPlans),
          );
        }
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "Unable to load gyms.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [canProvision]);

  async function searchGyms(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await loadGyms(search, undefined, status);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to load gyms.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const form = event.currentTarget;
    const values = new FormData(form);
    const planId = String(values.get("subscription_plan_id") ?? "");
    const plan = subscriptions.find((item) => item.id === planId);
    if (!plan) {
      setError(tx("Select an active subscription plan."));
      return;
    }
    const name = String(values.get("name") ?? "").trim();
    const ownerEmail = String(values.get("owner_email") ?? "").trim();
    const firstName = String(values.get("first_name") ?? "").trim();
    const lastName = String(values.get("last_name") ?? "").trim();
    if (!name || !ownerEmail || !firstName || !lastName) {
      setError(tx("Complete all required gym and owner fields."));
      return;
    }
    const request: GymProvisionRequest = {
      gym: {
        name,
        legal_name: name,
        email: String(values.get("gym_email") ?? "").trim(),
        phone: String(values.get("gym_phone") ?? "").trim(),
        address_line1: String(values.get("address_line1") ?? "").trim(),
        city: String(values.get("city") ?? "").trim(),
        region: String(values.get("region") ?? "").trim(),
        country_code: String(values.get("country_code") ?? "")
          .trim()
          .toUpperCase(),
        timezone: String(values.get("timezone") ?? "Asia/Yangon"),
        currency: plan.currency,
        subscription_plan_id: plan.id,
        subscription_status: "TRIAL",
      },
      owner: {
        email: ownerEmail,
        first_name: firstName,
        last_name: lastName,
        phone: String(values.get("owner_phone") ?? "").trim(),
        send_invitation: true,
      },
    };
    idempotencyKey.current ??= globalThis.crypto.randomUUID();
    setPending(true);
    try {
      const result = await provisionGym(request, idempotencyKey.current);
      setSuccess(
        tx("{code} was provisioned and an Owner setup email was sent.", {
          code: result.gym.code,
        }),
      );
      idempotencyKey.current = null;
      setAddress({ region: "", city: "" });
      setProvisioning(false);
      form.reset();
      await loadGyms(search, undefined, status);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to provision the gym.",
      );
    } finally {
      setPending(false);
    }
  }

  function applyStatus(next: string) {
    setStatus(next);
    setError(null);
    setLoading(true);
    void loadGyms(search, undefined, next)
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error ? reason.message : "Unable to load gyms.",
        );
      })
      .finally(() => setLoading(false));
  }

  return (
    <div className="grid gap-6">
      {canProvision && provisioning ? (
        <FormDialog
          description="Creates the gym, initial Owner, roles, and required defaults atomically. An Owner setup email is sent to the address below. No password or invitation link is shown here."
          onClose={() => {
            if (!pending) setProvisioning(false);
          }}
          onSubmit={submit}
          pending={pending}
          title={tx("Provision gym")}
          wide
        >
          <div className="grid gap-5">
            <section className="grid gap-3">
              <h3 className="text-xs font-bold tracking-[0.16em] text-slate-500 uppercase">
                {" "}
                {tx("Gym")}{" "}
              </h3>
              <p className="muted text-sm">
                {" "}
                {tx("A gym code is assigned from the gym name.")}{" "}
              </p>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="field md:col-span-2">
                  <span>{tx("Gym name *")}</span>
                  <input
                    className="input"
                    name="name"
                    required
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  <span>{tx("Subscription *")}</span>
                  <select
                    className="input"
                    name="subscription_plan_id"
                    required
                    disabled={pending || subscriptions.length === 0}
                  >
                    <option value="">{tx("Select a plan")}</option>
                    {subscriptions.map((plan) => (
                      <option key={plan.id} value={plan.id}>
                        {plan.name} ({tx(plan.currency)})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>{tx("Timezone *")}</span>
                  <input
                    className="input"
                    name="timezone"
                    defaultValue="Asia/Yangon"
                    required
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  <span>{tx("Gym email")}</span>
                  <input
                    className="input"
                    name="gym_email"
                    type="email"
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  <span>{tx("Gym phone")}</span>
                  <input
                    className="input"
                    name="gym_phone"
                    disabled={pending}
                  />
                </label>
                <label className="field md:col-span-2">
                  <span>{tx("Address")}</span>
                  <input
                    className="input"
                    name="address_line1"
                    disabled={pending}
                  />
                </label>
                <MyanmarRegionCityFields
                  city={address.city}
                  disabled={pending}
                  onChange={setAddress}
                  region={address.region}
                />
                <label className="field">
                  <span>{tx("Country code")}</span>
                  <input
                    className="input"
                    name="country_code"
                    maxLength={2}
                    placeholder="MM"
                    disabled={pending}
                  />
                </label>
              </div>
            </section>
            <section className="grid gap-3 border-t border-slate-200 pt-5">
              <h3 className="text-xs font-bold tracking-[0.16em] text-slate-500 uppercase">
                {" "}
                {tx("Owner")}{" "}
              </h3>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="field">
                  <span>{tx("Owner first name *")}</span>
                  <input
                    className="input"
                    name="first_name"
                    required
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  <span>{tx("Owner last name *")}</span>
                  <input
                    className="input"
                    name="last_name"
                    required
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  <span>{tx("Owner email *")}</span>
                  <input
                    className="input"
                    name="owner_email"
                    type="email"
                    required
                    disabled={pending}
                    aria-describedby="owner-email-delivery"
                  />
                </label>
                <label className="field">
                  <span>{tx("Owner phone")}</span>
                  <input
                    className="input"
                    name="owner_phone"
                    disabled={pending}
                  />
                </label>
              </div>
              <p id="owner-email-delivery" className="muted text-sm">
                {" "}
                {tx(
                  "Required. The Owner setup link is emailed to this address.",
                )}{" "}
              </p>
            </section>
            <button
              className="button button-create"
              type="submit"
              disabled={pending}
            >
              {pending
                ? tx("Provisioning…")
                : tx("Provision gym and email Owner")}
            </button>
          </div>
        </FormDialog>
      ) : null}

      <section
        aria-labelledby="gyms-title"
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="flex flex-col gap-4 border-b border-slate-200 px-4 py-4 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-lg font-bold text-slate-900" id="gyms-title">
              {" "}
              {tx("Directory")}{" "}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {loading && gyms.length === 0
                ? tx("Loading gyms.")
                : gyms.length === 1
                  ? tx("1 gym in this list.")
                  : tx("{count} gyms in this list.", { count: gyms.length })}
            </p>
          </div>
          {canProvision ? (
            <button
              className="button button-create"
              onClick={() => {
                setAddress({ region: "", city: "" });
                setProvisioning(true);
              }}
              type="button"
            >
              {" "}
              {tx("Provision gym")}{" "}
            </button>
          ) : null}
        </div>
        <div className="grid gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-4 sm:px-5">
          <div
            aria-label={tx("Gym status")}
            className="flex flex-wrap gap-2"
            role="group"
          >
            {STATUS_FILTERS.map((filter) => {
              const selected = status === filter.value;
              return (
                <button
                  aria-pressed={selected}
                  className={
                    selected
                      ? "rounded-full border border-slate-900 bg-slate-900 px-3.5 py-1.5 text-sm font-bold text-white"
                      : "rounded-full border border-slate-300 bg-white px-3.5 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-100"
                  }
                  key={filter.label}
                  onClick={() => applyStatus(filter.value)}
                  type="button"
                >
                  {tx(filter.label)}
                </button>
              );
            })}
          </div>
          <form className="flex gap-2" onSubmit={searchGyms}>
            <label className="field min-w-0 flex-1">
              <span className="sr-only">{tx("Search gyms")}</span>
              <input
                className="input"
                onChange={(event) => setSearch(event.target.value)}
                placeholder={tx("Search by name or code")}
                value={search}
              />
            </label>
            <button className="button" disabled={loading} type="submit">
              {" "}
              {tx("Search")}{" "}
            </button>
          </form>
        </div>
        {error ? (
          <p className="error mx-4 mt-4 sm:mx-5" role="alert">
            {tx(error)}
          </p>
        ) : null}
        {success ? (
          <p
            className="mx-4 mt-4 text-sm font-medium text-emerald-700 sm:mx-5"
            role="status"
          >
            {tx(success)}
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                <th className="px-5 py-3" scope="col">
                  {" "}
                  {tx("Gym")}{" "}
                </th>
                <th className="px-5 py-3" scope="col">
                  {" "}
                  {tx("Status")}{" "}
                </th>
                <th className="px-5 py-3" scope="col">
                  {" "}
                  {tx("Plan")}{" "}
                </th>
                <th className="px-5 py-3" scope="col">
                  {" "}
                  {tx("Subscription")}{" "}
                </th>
                <th className="px-5 py-3" scope="col">
                  {" "}
                  {tx("Currency")}{" "}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {gyms.map((gym) => (
                <tr className="bg-white hover:bg-slate-50" key={gym.id}>
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-3">
                      <span
                        aria-hidden="true"
                        className="grid size-10 shrink-0 place-items-center rounded-full bg-slate-100 text-xs font-black text-slate-600"
                      >
                        {initials(gym.name)}
                      </span>
                      <span className="min-w-0">
                        <Link
                          className="font-bold text-slate-900"
                          href={`/super-admin/gyms/${gym.id}`}
                        >
                          {gym.name}
                        </Link>
                        <Link
                          className="mt-0.5 block text-xs font-medium text-slate-500"
                          href={`/super-admin/gyms/${gym.id}`}
                        >
                          {gym.code}
                        </Link>
                      </span>
                    </div>
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge value={gym.status} />
                  </td>
                  <td className="px-5 py-4 text-slate-700">
                    {gym.subscription_plan_name ?? "—"}
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge value={gym.subscription_status} />
                  </td>
                  <td className="px-5 py-4 font-medium text-slate-700">
                    {tx(gym.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && gyms.length === 0 ? (
            <p className="px-6 py-14 text-center">
              <span className="block font-semibold text-slate-800">
                {" "}
                {tx("No gyms found.")}{" "}
              </span>
              <span className="mt-1 block text-sm text-slate-500">
                {" "}
                {tx("Try another status or search.")}{" "}
              </span>
            </p>
          ) : null}
        </div>
        {nextAfter ? (
          <div className="border-t border-slate-100 px-4 py-4 text-center sm:px-5">
            <button
              className="button"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                void loadGyms(search, nextAfter, status)
                  .catch((reason: unknown) =>
                    setError(
                      reason instanceof Error
                        ? reason.message
                        : tx("Unable to load more gyms."),
                    ),
                  )
                  .finally(() => setLoading(false));
              }}
              type="button"
            >
              {" "}
              {tx("Load more")}{" "}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}

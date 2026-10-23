"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  platformGet,
  platformGetCollection,
  provisionGym,
} from "@/features/platform/client";
import type {
  GymProvisionRequest,
  PlatformGym,
  PlatformSubscription,
} from "@/types/api";

export function GymsManager({ canProvision }: { canProvision: boolean }) {
  const [gyms, setGyms] = useState<PlatformGym[]>([]);
  const [subscriptions, setSubscriptions] = useState<PlatformSubscription[]>(
    [],
  );
  const [search, setSearch] = useState("");
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const idempotencyKey = useRef<string | null>(null);

  const loadGyms = useCallback(async (query = "", after?: string) => {
    const parameters = new URLSearchParams();
    if (query.trim()) parameters.set("search", query.trim());
    if (after) parameters.set("after", after);
    const suffix = parameters.size ? `?${parameters.toString()}` : "";
    const body = await platformGetCollection<PlatformGym>(
      `admin/gyms${suffix}`,
    );
    setGyms((current) => (after ? [...current, ...body.data] : body.data));
    setNextAfter(body.meta.next_after ?? null);
  }, []);

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
          setSubscriptions(plans.filter((plan) => plan.status === "ACTIVE"));
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
      await loadGyms(search);
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
      setError("Select an active subscription plan.");
      return;
    }
    const code = String(values.get("code") ?? "")
      .trim()
      .toUpperCase();
    const name = String(values.get("name") ?? "").trim();
    const ownerEmail = String(values.get("owner_email") ?? "").trim();
    const firstName = String(values.get("first_name") ?? "").trim();
    const lastName = String(values.get("last_name") ?? "").trim();
    if (!code || !name || !ownerEmail || !firstName || !lastName) {
      setError("Complete all required gym and owner fields.");
      return;
    }
    const request: GymProvisionRequest = {
      gym: {
        code,
        name,
        legal_name: String(values.get("legal_name") ?? "").trim(),
        email: String(values.get("gym_email") ?? "").trim(),
        phone: String(values.get("gym_phone") ?? "").trim(),
        address_line1: String(values.get("address_line1") ?? "").trim(),
        city: String(values.get("city") ?? "").trim(),
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
        `${result.gym.code} was provisioned and an Owner setup email was sent.`,
      );
      idempotencyKey.current = null;
      form.reset();
      await loadGyms(search);
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

  return (
    <div className="grid gap-6 xl:grid-cols-[24rem_1fr]">
      {canProvision && (
        <section className="card" aria-labelledby="provision-title">
          <h2 id="provision-title" className="text-xl font-bold">
            Provision gym
          </h2>
          <p className="muted mt-2 text-sm">
            Creates the gym, initial Owner, roles, and required defaults
            atomically. An Owner setup email is sent to the address below. No
            password or invitation link is shown here.
          </p>
          <form className="mt-5 grid gap-3" onSubmit={submit}>
            <label className="field">
              <span>Gym code *</span>
              <input
                className="input"
                name="code"
                required
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Gym name *</span>
              <input
                className="input"
                name="name"
                required
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Legal name</span>
              <input className="input" name="legal_name" disabled={pending} />
            </label>
            <label className="field">
              <span>Subscription *</span>
              <select
                className="input"
                name="subscription_plan_id"
                required
                disabled={pending || subscriptions.length === 0}
              >
                <option value="">Select a plan</option>
                {subscriptions.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name} ({plan.currency})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Timezone *</span>
              <input
                className="input"
                name="timezone"
                defaultValue="Asia/Yangon"
                required
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Gym email</span>
              <input
                className="input"
                name="gym_email"
                type="email"
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Gym phone</span>
              <input className="input" name="gym_phone" disabled={pending} />
            </label>
            <label className="field">
              <span>Address</span>
              <input
                className="input"
                name="address_line1"
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>City</span>
              <input className="input" name="city" disabled={pending} />
            </label>
            <label className="field">
              <span>Country code</span>
              <input
                className="input"
                name="country_code"
                maxLength={2}
                placeholder="MM"
                disabled={pending}
              />
            </label>
            <hr className="my-2 border-slate-200" />
            <label className="field">
              <span>Owner first name *</span>
              <input
                className="input"
                name="first_name"
                required
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Owner last name *</span>
              <input
                className="input"
                name="last_name"
                required
                disabled={pending}
              />
            </label>
            <label className="field">
              <span>Owner email *</span>
              <input
                className="input"
                name="owner_email"
                type="email"
                required
                disabled={pending}
                aria-describedby="owner-email-delivery"
              />
            </label>
            <p id="owner-email-delivery" className="muted text-sm">
              Required. The Owner setup link is emailed to this address.
            </p>
            <label className="field">
              <span>Owner phone</span>
              <input className="input" name="owner_phone" disabled={pending} />
            </label>
            <button
              className="button button-create"
              type="submit"
              disabled={pending}
            >
              {pending ? "Provisioning…" : "Provision gym and email Owner"}
            </button>
          </form>
        </section>
      )}

      <section className="card min-w-0" aria-labelledby="gyms-title">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="gyms-title" className="text-xl font-bold">
              Gyms
            </h2>
            <p className="muted mt-1 text-sm">Platform-managed tenant gyms.</p>
          </div>
          <form className="flex gap-2" onSubmit={searchGyms}>
            <label className="field">
              <span className="sr-only">Search gyms</span>
              <input
                className="input"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search gyms"
              />
            </label>
            <button className="button" type="submit" disabled={loading}>
              Search
            </button>
          </form>
        </div>
        {error && (
          <p className="error mt-4" role="alert">
            {error}
          </p>
        )}
        {success && (
          <p
            className="mt-4 text-sm font-medium text-emerald-700"
            role="status"
          >
            {success}
          </p>
        )}
        <div className="mt-5 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="py-2 pr-4">Code</th>
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Plan</th>
                <th className="py-2">Currency</th>
              </tr>
            </thead>
            <tbody>
              {gyms.map((gym) => (
                <tr key={gym.id} className="border-b border-slate-100">
                  <td className="py-3 pr-4 font-medium">
                    <Link
                      className="text-blue-700 hover:underline"
                      href={`/super-admin/gyms/${gym.id}`}
                    >
                      {gym.code}
                    </Link>
                  </td>
                  <td className="py-3 pr-4">
                    <Link
                      className="text-blue-700 hover:underline"
                      href={`/super-admin/gyms/${gym.id}`}
                    >
                      {gym.name}
                    </Link>
                  </td>
                  <td className="py-3 pr-4">{gym.status}</td>
                  <td className="py-3 pr-4">
                    {gym.subscription_plan_name ?? "—"}
                  </td>
                  <td className="py-3">{gym.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && gyms.length === 0 && (
            <p className="muted py-6 text-center">No gyms found.</p>
          )}
        </div>
        {nextAfter && (
          <button
            className="button mt-4"
            type="button"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              void loadGyms(search, nextAfter)
                .catch((reason: unknown) =>
                  setError(
                    reason instanceof Error
                      ? reason.message
                      : "Unable to load more gyms.",
                  ),
                )
                .finally(() => setLoading(false));
            }}
          >
            Load more
          </button>
        )}
      </section>
    </div>
  );
}

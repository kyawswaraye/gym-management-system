"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member, Membership, MembershipPlan } from "@/types/api";

export function MembershipsManager({
  canCreate,
  canUpdate,
  canCancel,
  currency,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canCancel: boolean;
  currency: string;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [plans, setPlans] = useState<MembershipPlan[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const [memberRows, planRows, membershipRows] = await Promise.all([
        operationGet<Member[]>("members/"),
        operationGet<MembershipPlan[]>("membership-plans/"),
        operationGet<Membership[]>("memberships/"),
      ]);
      setMembers(memberRows);
      setPlans(planRows);
      setMemberships(membershipRows);
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to load memberships.",
      );
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      await operationMutation<MembershipPlan>("membership-plans/", {
        code: form.get("code"),
        name: form.get("name"),
        duration_value: Number(form.get("duration_value")),
        duration_unit: form.get("duration_unit"),
        price: form.get("price"),
        currency: form.get("currency"),
      });
      event.currentTarget.reset();
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to create plan.",
      );
    } finally {
      setPending(false);
    }
  }

  async function createMembership(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      await operationMutation<Membership>("memberships/", {
        member_id: form.get("member_id"),
        plan_id: form.get("plan_id"),
        start_date: form.get("start_date"),
        discount_amount: form.get("discount_amount") || "0.00",
      });
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to create membership.",
      );
    } finally {
      setPending(false);
    }
  }

  async function membershipAction(
    membership: Membership,
    action: "suspend" | "resume" | "cancel",
  ) {
    setPending(true);
    try {
      await operationMutation(
        `memberships/${membership.id}/${action}/`,
        action === "cancel" ? { reason: "Cancelled by staff" } : {},
      );
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to update membership.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {canCreate && (
        <div className="grid gap-6 xl:grid-cols-2">
          <form className="card grid gap-4 p-5" onSubmit={createPlan}>
            <h2 className="text-lg font-bold">New plan</h2>
            <div className="grid grid-cols-2 gap-3">
              <label className="field">
                Code
                <input className="input" name="code" required />
              </label>
              <label className="field">
                Name
                <input className="input" name="name" required />
              </label>
              <label className="field">
                Duration
                <input
                  className="input"
                  min="1"
                  name="duration_value"
                  required
                  type="number"
                />
              </label>
              <label className="field">
                Unit
                <select className="input" name="duration_unit">
                  <option value="MONTH">Month</option>
                  <option value="DAY">Day</option>
                  <option value="WEEK">Week</option>
                  <option value="YEAR">Year</option>
                </select>
              </label>
              <label className="field">
                Price
                <input
                  className="input"
                  min="0"
                  name="price"
                  required
                  step="0.01"
                  type="number"
                />
              </label>
              <label className="field">
                Currency
                <input
                  className="input"
                  defaultValue={currency}
                  maxLength={3}
                  name="currency"
                  required
                />
              </label>
            </div>
            <button className="button" disabled={pending} type="submit">
              Save plan
            </button>
          </form>
          <form className="card grid gap-4 p-5" onSubmit={createMembership}>
            <h2 className="text-lg font-bold">Start membership</h2>
            <label className="field">
              Member
              <select className="input" name="member_id" required>
                <option value="">Select member</option>
                {members.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.member_number} — {member.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Plan
              <select className="input" name="plan_id" required>
                <option value="">Select plan</option>
                {plans
                  .filter((plan) => plan.is_active)
                  .map((plan) => (
                    <option key={plan.id} value={plan.id}>
                      {plan.name} — {plan.price} {plan.currency}
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              Start date
              <input
                className="input"
                defaultValue={new Date().toISOString().slice(0, 10)}
                name="start_date"
                required
                type="date"
              />
            </label>
            <label className="field">
              Discount
              <input
                className="input"
                defaultValue="0.00"
                min="0"
                name="discount_amount"
                step="0.01"
                type="number"
              />
            </label>
            <button className="button" disabled={pending} type="submit">
              Start membership
            </button>
          </form>
        </div>
      )}
      <section className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="p-3">Member</th>
              <th className="p-3">Plan</th>
              <th className="p-3">Dates</th>
              <th className="p-3">Balance</th>
              <th className="p-3">Status</th>
              <th className="p-3">Action</th>
            </tr>
          </thead>
          <tbody>
            {memberships.map((membership) => (
              <tr className="border-t border-slate-100" key={membership.id}>
                <td className="p-3">{membership.member_name}</td>
                <td className="p-3">{membership.plan_name}</td>
                <td className="p-3">
                  {membership.start_date} – {membership.expiry_date}
                </td>
                <td className="p-3">
                  {membership.outstanding_amount} {membership.currency}
                </td>
                <td className="p-3">
                  {membership.status} / {membership.payment_status}
                </td>
                <td className="p-3">
                  {canUpdate && membership.status === "ACTIVE" && (
                    <button
                      className="underline"
                      onClick={() =>
                        void membershipAction(membership, "suspend")
                      }
                      type="button"
                    >
                      Suspend
                    </button>
                  )}
                  {canUpdate && membership.status === "SUSPENDED" && (
                    <button
                      className="underline"
                      onClick={() =>
                        void membershipAction(membership, "resume")
                      }
                      type="button"
                    >
                      Resume
                    </button>
                  )}
                  {canCancel &&
                    !["CANCELLED", "EXPIRED"].includes(membership.status) && (
                      <button
                        className="ml-3 underline"
                        onClick={() =>
                          void membershipAction(membership, "cancel")
                        }
                        type="button"
                      >
                        Cancel
                      </button>
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

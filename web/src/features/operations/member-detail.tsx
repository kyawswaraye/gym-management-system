"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member, Membership, Payment } from "@/types/api";

type Summary = {
  member: Member;
  current_membership: Membership | null;
  membership_history: Membership[];
  payment_history: Payment[];
};

export function MemberDetail({
  memberId,
  canUpdate,
}: {
  memberId: string;
  canUpdate: boolean;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setSummary(await operationGet<Summary>(`members/${memberId}/summary/`));
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load member.",
      );
    }
  }, [memberId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await operationMutation<Member>(
        `members/${memberId}/`,
        {
          first_name: form.get("first_name"),
          last_name: form.get("last_name"),
          phone: form.get("phone"),
          email: form.get("email"),
          notes: form.get("notes"),
        },
        "PATCH",
      );
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to update member.",
      );
    }
  }

  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!summary) return <p className="muted">Loading member…</p>;
  const member = summary.member;
  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      <form className="card grid gap-4 p-5" onSubmit={save}>
        <p className="muted">{member.member_number}</p>
        <label className="field">
          First name
          <input
            className="input"
            defaultValue={member.first_name}
            disabled={!canUpdate}
            name="first_name"
          />
        </label>
        <label className="field">
          Last name
          <input
            className="input"
            defaultValue={member.last_name}
            disabled={!canUpdate}
            name="last_name"
          />
        </label>
        <label className="field">
          Phone
          <input
            className="input"
            defaultValue={member.phone}
            disabled={!canUpdate}
            name="phone"
          />
        </label>
        <label className="field">
          Email
          <input
            className="input"
            defaultValue={member.email}
            disabled={!canUpdate}
            name="email"
            type="email"
          />
        </label>
        <label className="field">
          Notes
          <textarea
            className="input"
            defaultValue={member.notes}
            disabled={!canUpdate}
            name="notes"
          />
        </label>
        {canUpdate && <button className="button">Save changes</button>}
      </form>
      <div className="grid gap-6">
        <section className="card p-5">
          <h2 className="font-bold">Current membership</h2>
          {summary.current_membership ? (
            <p className="mt-3">
              {summary.current_membership.plan_name}:{" "}
              {summary.current_membership.start_date} –{" "}
              {summary.current_membership.expiry_date} (
              {summary.current_membership.payment_status})
            </p>
          ) : (
            <p className="muted mt-3">No membership yet.</p>
          )}
        </section>
        <section className="card p-5">
          <h2 className="font-bold">Recent payments</h2>
          <ul className="mt-3 grid gap-2">
            {summary.payment_history.map((payment) => (
              <li className="flex justify-between" key={payment.id}>
                <span>{payment.description}</span>
                <strong>
                  {payment.amount} {payment.currency}
                </strong>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

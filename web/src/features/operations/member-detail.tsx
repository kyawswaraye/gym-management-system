"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member, MemberSummary } from "@/types/api";

function dayLabel(days: number): string {
  return days === 1 ? "1 day remaining" : `${days} days remaining`;
}

export function MemberDetail({
  memberId,
  canUpdate,
}: {
  memberId: string;
  canUpdate: boolean;
}) {
  const [summary, setSummary] = useState<MemberSummary | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setSummary(
        await operationGet<MemberSummary>(`members/${memberId}/summary/`),
      );
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

  if (!summary && error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!summary) {
    return (
      <p className="muted" role="status">
        Loading member summary.
      </p>
    );
  }
  const member = summary.member;
  const payments = summary.payment_summary;
  const noFinalizedPayments =
    payments.total_charged === "0.00" && payments.total_refunded === "0.00";
  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      <form className="card grid gap-4 p-5" onSubmit={save}>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
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
            defaultValue={member.phone ?? ""}
            disabled={!canUpdate}
            name="phone"
          />
        </label>
        <label className="field">
          Email
          <input
            className="input"
            defaultValue={member.email ?? ""}
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
        <section
          aria-labelledby="current-membership-title"
          className="card p-5"
        >
          <h2 className="font-bold" id="current-membership-title">
            Current membership
          </h2>
          {summary.current_membership ? (
            <div className="mt-3 grid gap-1">
              <p>
                {summary.current_membership.plan_name}:{" "}
                {summary.current_membership.start_date} –{" "}
                {summary.current_membership.expiry_date} (
                {summary.current_membership.payment_status})
              </p>
              <p className="muted">
                {summary.days_remaining === null
                  ? "Days remaining are unavailable."
                  : dayLabel(summary.days_remaining)}
                {summary.is_pt_client ? " · PT client" : ""}
              </p>
            </div>
          ) : (
            <p className="muted mt-3">No current membership.</p>
          )}
        </section>
        <section aria-labelledby="payment-summary-title" className="card p-5">
          <h2 className="font-bold" id="payment-summary-title">
            Payment summary
          </h2>
          {payments.available ? (
            <dl className="mt-3 grid gap-2">
              <div className="flex justify-between gap-4">
                <dt>Total charged</dt>
                <dd>
                  {payments.total_charged} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Total refunded</dt>
                <dd>
                  {payments.total_refunded} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Net paid</dt>
                <dd>
                  {payments.net_paid} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Outstanding membership balance</dt>
                <dd>
                  {payments.outstanding_membership_balance} {payments.currency}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="muted mt-3">Payment summary is not available.</p>
          )}
          {payments.available && noFinalizedPayments ? (
            <p className="muted mt-3">No finalized payments.</p>
          ) : null}
        </section>
        <section
          aria-labelledby="membership-history-title"
          className="card p-5"
        >
          <h2 className="font-bold" id="membership-history-title">
            Membership history
          </h2>
          {summary.membership_history.length === 0 ? (
            <p className="muted mt-3">No membership history.</p>
          ) : (
            <ul className="mt-3 grid gap-2">
              {summary.membership_history.map((membership) => (
                <li key={membership.id}>
                  {membership.plan_name}: {membership.start_date} –{" "}
                  {membership.expiry_date} ({membership.status})
                </li>
              ))}
            </ul>
          )}
          {summary.membership_history_has_more ? (
            <p className="muted mt-3">Older memberships are not shown.</p>
          ) : null}
        </section>
        <section aria-labelledby="check-in-summary-title" className="card p-5">
          <h2 className="font-bold" id="check-in-summary-title">
            Check-ins
          </h2>
          <p className="muted mt-3">
            {summary.check_in_summary.available
              ? "Check-in summary is available."
              : "Check-in summary is not available yet."}
          </p>
        </section>
      </div>
    </div>
  );
}

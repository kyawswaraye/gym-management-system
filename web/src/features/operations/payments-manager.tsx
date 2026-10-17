"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member, Payment } from "@/types/api";

/** Unpublished payment-screen membership shape. Not the published Membership contract. */
type UnpublishedPaymentMembership = {
  id: string;
  member: string;
  outstanding_amount: string;
};

export function PaymentsManager({
  canCreate,
  canSubmit,
  canApprove,
  canUpdate,
  canVoid,
  canRefund,
  currency,
}: {
  canCreate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canUpdate: boolean;
  canVoid: boolean;
  canRefund: boolean;
  currency: string;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [memberships, setMemberships] = useState<
    UnpublishedPaymentMembership[]
  >([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const [memberRows, membershipRows, paymentRows] = await Promise.all([
        operationGet<Member[]>("members/"),
        operationGet<UnpublishedPaymentMembership[]>("memberships/"),
        operationGet<Payment[]>("payments/"),
      ]);
      setMembers(memberRows);
      setMemberships(membershipRows);
      setPayments(paymentRows);
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load payments.",
      );
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function createPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = new FormData(event.currentTarget);
    const memberId = String(form.get("member_id"));
    const membership = memberships.find(
      (row) => row.member === memberId && row.outstanding_amount !== "0.00",
    );
    try {
      await operationMutation<Payment>("payments/", {
        member_id: memberId,
        membership_id: membership?.id ?? null,
        purpose: "MEMBERSHIP",
        description: form.get("description"),
        amount: form.get("amount"),
        currency,
        method: form.get("method"),
        business_date: form.get("business_date"),
        received_at: new Date().toISOString(),
      });
      event.currentTarget.reset();
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to create payment.",
      );
    } finally {
      setPending(false);
    }
  }

  async function action(
    payment: Payment,
    name: "submit" | "approve" | "reject" | "cancel" | "void" | "refund",
  ) {
    setPending(true);
    try {
      const body =
        name === "refund"
          ? { reason: "Full refund by staff", amount: payment.amount }
          : ["reject", "cancel", "void"].includes(name)
            ? { reason: `${name} by staff` }
            : {};
      await operationMutation(`payments/${payment.id}/${name}/`, body);
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to update payment.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      {canCreate && (
        <form className="card grid gap-4 p-5" onSubmit={createPayment}>
          <h2 className="text-lg font-bold">Record payment</h2>
          <label className="field">
            Member
            <select className="input" name="member_id" required>
              <option value="">Select member</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.member_number} — {member.first_name}{" "}
                  {member.last_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Description
            <input
              className="input"
              defaultValue="Membership payment"
              name="description"
              required
            />
          </label>
          <label className="field">
            Amount ({currency})
            <input
              className="input"
              min="0.01"
              name="amount"
              required
              step="0.01"
              type="number"
            />
          </label>
          <label className="field">
            Method
            <select className="input" name="method">
              <option value="CASH">Cash</option>
              <option value="BANK_TRANSFER">Bank transfer</option>
              <option value="CARD">Card</option>
              <option value="MOBILE_PAYMENT">Mobile payment</option>
              <option value="OTHER">Other</option>
            </select>
          </label>
          <label className="field">
            Business date
            <input
              className="input"
              defaultValue={new Date().toISOString().slice(0, 10)}
              name="business_date"
              required
              type="date"
            />
          </label>
          <button className="button" disabled={pending} type="submit">
            Save draft
          </button>
        </form>
      )}
      <section className="card overflow-x-auto">
        {error && (
          <p className="error m-4" role="alert">
            {error}
          </p>
        )}
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="p-3">Member</th>
              <th className="p-3">Amount</th>
              <th className="p-3">Method</th>
              <th className="p-3">Status</th>
              <th className="p-3">Action</th>
            </tr>
          </thead>
          <tbody>
            {payments.map((payment) => (
              <tr className="border-t border-slate-100" key={payment.id}>
                <td className="p-3">{payment.member_name}</td>
                <td className="p-3">
                  {payment.amount} {payment.currency}
                </td>
                <td className="p-3">{payment.method}</td>
                <td className="p-3">{payment.status}</td>
                <td className="p-3">
                  {payment.status === "DRAFT" && canSubmit && (
                    <button
                      className="button"
                      disabled={pending}
                      onClick={() => void action(payment, "submit")}
                      type="button"
                    >
                      Submit
                    </button>
                  )}
                  {payment.status === "PENDING_APPROVAL" && canApprove && (
                    <button
                      className="button"
                      disabled={pending}
                      onClick={() => void action(payment, "approve")}
                      type="button"
                    >
                      Approve
                    </button>
                  )}
                  {payment.status === "PENDING_APPROVAL" && canApprove && (
                    <button
                      className="ml-2 underline"
                      disabled={pending}
                      onClick={() => void action(payment, "reject")}
                      type="button"
                    >
                      Reject
                    </button>
                  )}
                  {payment.status === "DRAFT" && canUpdate && (
                    <button
                      className="ml-2 underline"
                      disabled={pending}
                      onClick={() => void action(payment, "cancel")}
                      type="button"
                    >
                      Cancel
                    </button>
                  )}
                  {payment.status === "POSTED" && canVoid && (
                    <button
                      className="ml-2 underline"
                      disabled={pending}
                      onClick={() => void action(payment, "void")}
                      type="button"
                    >
                      Void
                    </button>
                  )}
                  {payment.status === "POSTED" && canRefund && (
                    <button
                      className="ml-2 underline"
                      disabled={pending}
                      onClick={() => void action(payment, "refund")}
                      type="button"
                    >
                      Full refund
                    </button>
                  )}
                  {payment.receipt_number && (
                    <span>{payment.receipt_number}</span>
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

"use client";

import { useRef, useState } from "react";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
  SelectInput,
  ServerFacts,
  StatusText,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import {
  getPtCommission,
  listPtCommissions,
  recordPtCommissionPayment,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import type {
  PaymentMethod,
  PTCommission,
  PTCommissionDetail,
} from "@/types/api";

const PAGE = "25";
const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;

export function CommissionsManager({
  canPay,
  currency,
  today,
  membershipId,
}: {
  canPay: boolean;
  currency: string;
  today: string;
  membershipId: string;
}) {
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<PTCommissionDetail | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payoutDate, setPayoutDate] = useState(today);
  const [payoutMethod, setPayoutMethod] = useState<PaymentMethod>("CASH");
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const rows = useCursorPages<PTCommission>(`commissions:${status}`, (after) =>
    listPtCommissions({
      limit: PAGE,
      ...(after ? { after } : {}),
      ...(status ? { status } : {}),
    }),
  );
  const cashPayout = paying && payoutMethod === "CASH";
  const sessions = useOpenCashSessions(cashPayout ? payoutDate : "");
  const sessionsBlocked =
    cashPayout && (sessions.pending || Boolean(sessions.error) || !payoutDate);

  async function openRow(commissionId: string) {
    setPending(true);
    setError("");
    try {
      setSelected(await getPtCommission(commissionId));
    } catch (caught) {
      setError(phase5Error(caught, "The commission could not be loaded."));
    } finally {
      setPending(false);
    }
  }

  const outstanding =
    selected !== null &&
    selected.status !== "PAID" &&
    selected.status !== "VOID" &&
    selected.outstanding_amount !== "0.00";
  const payerDiffers = selected?.created_by_membership_id !== membershipId;

  return (
    <div className="grid gap-4">
      <Alert message={error || rows.error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <Field label="Commission status">
        <SelectInput
          onChange={(event) => setStatus(event.target.value)}
          value={status}
        >
          <option value="">All</option>
          <option value="ACCRUED">Outstanding · accrued</option>
          <option value="PARTIALLY_PAID">Outstanding · partially paid</option>
          <option value="PAID">PAID</option>
          <option value="VOID">VOID</option>
        </SelectInput>
      </Field>
      {rows.pending || pending ? (
        <Loading label="Loading commissions." />
      ) : null}
      {!rows.pending && rows.rows.length === 0 ? (
        <Empty label="No commissions match." />
      ) : (
        <ul className="grid gap-3">
          {rows.rows.map((row) => (
            <li key={row.id}>
              <button
                className="underline"
                onClick={() => void openRow(row.id)}
                type="button"
              >
                Outstanding {row.outstanding_amount} {row.currency}
              </button>{" "}
              <StatusText status={row.status} />
            </li>
          ))}
        </ul>
      )}
      <CursorControls
        bounded={rows.bounded}
        hasMore={rows.hasMore}
        index={rows.index}
        onNext={() => void rows.goNext()}
        onPrevious={() => void rows.goPrevious()}
        pending={rows.pending}
      />
      {selected ? (
        <section
          aria-labelledby="commission-detail-title"
          className="grid gap-3"
        >
          <h2 className="text-xl font-semibold" id="commission-detail-title">
            Commission detail
          </h2>
          <ServerFacts
            rows={[
              { label: "Status", value: selected.status },
              { label: "Commission", value: selected.commission_amount },
              { label: "Paid", value: selected.paid_amount },
              { label: "Outstanding", value: selected.outstanding_amount },
              { label: "Gym share", value: selected.gym_share },
            ]}
          />
          <h3 className="font-semibold">Payments</h3>
          {selected.payments.length === 0 ? (
            <Empty label="No payouts have been recorded." />
          ) : (
            <ul className="grid gap-2">
              {selected.payments.map((payment) => (
                <li key={payment.id}>
                  {payment.amount} {payment.payment_method} ·{" "}
                  {payment.business_date} · {payment.status}
                </li>
              ))}
            </ul>
          )}
          {canPay && outstanding && payerDiffers ? (
            <button
              className="button"
              onClick={() => {
                setPayoutDate(today);
                setPayoutMethod("CASH");
                setPaying(true);
              }}
              type="button"
            >
              Record payout
            </button>
          ) : null}
        </section>
      ) : null}
      {paying && selected && canPay ? (
        <form
          aria-labelledby="payout-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const method = String(
              form.get("payment_method") ?? "CASH",
            ) as PaymentMethod;
            const amount = String(form.get("amount") ?? "");
            const businessDate = String(form.get("business_date") ?? "");
            const cashSessionId = String(form.get("cash_session_id") ?? "");
            if (method === "CASH" && (sessions.error || !cashSessionId)) {
              setError(sessions.error || "Select an open cash session.");
              return;
            }
            const body = {
              amount,
              business_date: businessDate,
              payment_method: method,
              reference_number: String(form.get("reference_number") ?? ""),
              policy_note: String(form.get("policy_note") ?? ""),
              ...(method === "CASH"
                ? { cash_session_id: cashSessionId }
                : { cash_session_id: null }),
            };
            const fingerprint = JSON.stringify({ id: selected.id, ...body });
            if (!key.current || key.current.fingerprint !== fingerprint) {
              key.current = { fingerprint, value: crypto.randomUUID() };
            }
            setPending(true);
            setError("");
            void recordPtCommissionPayment(selected.id, body, key.current.value)
              .then(async (result) => {
                key.current = null;
                setNotice("Payout recorded.");
                setPaying(false);
                setSelected(await getPtCommission(result.commission_after.id));
                rows.reload();
              })
              .catch((caught: unknown) => {
                setError(
                  phase5Error(caught, "The payout could not be recorded."),
                );
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="payout-title">
            Record payout
          </h3>
          <Field label="Amount">
            <TextInput
              defaultValue={selected.outstanding_amount}
              name="amount"
              required
            />
          </Field>
          <p className="muted">
            Use the outstanding amount for a full payout, or a smaller amount
            for a partial payout. Currency {currency}.
          </p>
          <Field label="Business date">
            <TextInput
              name="business_date"
              onChange={(event) => setPayoutDate(event.target.value)}
              required
              type="date"
              value={payoutDate}
            />
          </Field>
          <Field label="Method">
            <SelectInput
              name="payment_method"
              onChange={(event) =>
                setPayoutMethod(event.target.value as PaymentMethod)
              }
              value={payoutMethod}
            >
              {METHODS.map((method) => (
                <option key={method} value={method}>
                  {method}
                </option>
              ))}
            </SelectInput>
          </Field>
          {payoutMethod === "CASH" ? (
            <CashSessionSelect
              collectionError={sessions.error}
              currency={currency}
              label="Cash session when the method is cash"
              pending={sessions.pending}
              resetKey={payoutDate}
              sessions={sessions.sessions}
            />
          ) : (
            <p className="muted">
              This method is not cash, so no cash session is sent.
            </p>
          )}
          <Field label="Reference">
            <TextInput name="reference_number" />
          </Field>
          <Field label="Policy note">
            <TextInput name="policy_note" />
          </Field>
          <button
            className="button"
            disabled={pending || sessionsBlocked}
            type="submit"
          >
            Save payout
          </button>
        </form>
      ) : null}
    </div>
  );
}

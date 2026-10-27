"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from "react";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import { formatAppDate } from "@/lib/date";
import {
  Alert,
  CursorControls,
  Field,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import { SaleCommandDialog } from "@/features/pt/sale-command-dialog";
import {
  getPtCommission,
  listPtCommissions,
  recordPtCommissionPayment,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  PaymentMethod,
  PTCommission,
  PTCommissionDetail,
  PTCommissionPaymentWriteRequest,
} from "@/types/api";

const PAGE = "25";
const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function commissionStatusClass(status: string): string {
  switch (status) {
    case "ACCRUED":
      return "bg-amber-100 text-amber-900";
    case "PARTIALLY_PAID":
      return "bg-sky-100 text-sky-900";
    case "PAID":
    case "POSTED":
      return "bg-emerald-100 text-emerald-800";
    case "VOID":
      return "bg-red-100 text-red-800";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

function CommissionStatusBadge({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-bold ${commissionStatusClass(status)}`}
    >
      {tx(status)}
    </span>
  );
}

function moneyLabel(
  amount: string | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount == null || amount === "") return "—";
  const code = currency?.trim();
  return code ? `${amount} ${code}` : amount;
}

function Fact({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  const tx = useTx();
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2">
      <dt className="text-xs font-medium text-slate-500">{tx(label)}</dt>
      <dd
        className={`mt-0.5 break-words text-slate-900 ${
          emphasis ? "text-lg font-black tabular-nums" : "font-semibold"
        }`}
      >
        <IdentifierValue value={value} />
      </dd>
    </div>
  );
}

function NoticeBanner({ message }: { message: string }) {
  const tx = useTx();
  return (
    <p
      aria-live="polite"
      className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
      role="status"
    >
      {tx(message)}
    </p>
  );
}

function rowLabel(
  row: PTCommission,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  const detail = `${row.outstanding_amount}${
    row.currency ? ` ${tx(row.currency)}` : ""
  }`;
  return tx("Outstanding {detail}", { detail });
}

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
  const tx = useTx();
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<PTCommissionDetail | null>(null);
  const [requestedId, setRequestedId] = useState("");
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [detailPending, setDetailPending] = useState(false);
  const [commandPending, setCommandPending] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payoutDate, setPayoutDate] = useState(today);
  const [payoutMethod, setPayoutMethod] = useState<PaymentMethod>("CASH");
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const pendingRef = useRef(false);
  const commandToken = useRef(0);
  const detailToken = useRef(0);
  const dialogRef = useRef<HTMLFormElement>(null);
  const payButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
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
  const filtered = status !== "";
  const showPaging = rows.index > 0 || rows.hasMore || rows.bounded;
  const outstanding =
    selected !== null &&
    selected.status !== "PAID" &&
    selected.status !== "VOID" &&
    selected.outstanding_amount !== "0.00";
  const payerDiffers = selected?.created_by_membership_id !== membershipId;
  const showPayout = canPay && outstanding && payerDiffers;
  const balanceCurrency = selected?.currency || currency;

  useEffect(() => {
    if (paying) {
      restoreFocus.current = true;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!restoreFocus.current) return;
    restoreFocus.current = false;
    payButtonRef.current?.focus();
  }, [paying]);

  function beginCommand(): number {
    const token = ++commandToken.current;
    pendingRef.current = true;
    setCommandPending(true);
    return token;
  }

  function endCommand(token: number) {
    if (commandToken.current !== token) return;
    pendingRef.current = false;
    setCommandPending(false);
  }

  function closePayout() {
    if (pendingRef.current) return;
    setDialogError("");
    setPaying(false);
  }

  function openPayout() {
    if (pendingRef.current || paying || detailPending || !showPayout) return;
    setPayoutDate(today);
    setPayoutMethod("CASH");
    setDialogError("");
    setNotice("");
    setPaying(true);
  }

  async function openRow(commissionId: string) {
    const token = ++detailToken.current;
    setDetailPending(true);
    setLoadError("");
    setNotice("");
    setRequestedId(commissionId);
    try {
      const detail = await getPtCommission(commissionId);
      if (detailToken.current !== token) return;
      setSelected(detail);
    } catch (caught) {
      if (detailToken.current !== token) return;
      setLoadError(
        phase5Error(caught, tx("The commission could not be loaded.")),
      );
    } finally {
      if (detailToken.current === token) setDetailPending(false);
    }
  }

  function savePayout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !canPay || pendingRef.current) return;
    const form = new FormData(event.currentTarget);
    const method = String(
      form.get("payment_method") ?? "CASH",
    ) as PaymentMethod;
    const amount = String(form.get("amount") ?? "");
    const businessDate = String(form.get("business_date") ?? "");
    const cashSessionId = String(form.get("cash_session_id") ?? "");
    if (!amount || !businessDate) {
      setDialogError("Enter the amount and business date.");
      return;
    }
    if (method === "CASH" && (sessions.error || !cashSessionId)) {
      setDialogError(sessions.error || "Select an open cash session.");
      return;
    }
    const body: PTCommissionPaymentWriteRequest = {
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
    const idempotencyKey = key.current.value;
    const token = beginCommand();
    setDialogError("");
    void recordPtCommissionPayment(selected.id, body, idempotencyKey)
      .then((result) => {
        if (commandToken.current !== token) return;
        key.current = null;
        setDialogError("");
        setNotice(tx("Payout recorded."));
        setPaying(false);
        rows.reload();
        void getPtCommission(result.commission_after.id)
          .then((next) => {
            if (commandToken.current !== token) return;
            setSelected(next);
            setRequestedId(next.id);
            setLoadError("");
          })
          .catch((caught: unknown) => {
            if (commandToken.current !== token) return;
            setLoadError(
              phase5Error(caught, tx("The commission could not be loaded.")),
            );
          });
      })
      .catch((caught: unknown) => {
        if (commandToken.current !== token) return;
        setDialogError(
          phase5Error(caught, tx("The payout could not be recorded.")),
        );
      })
      .finally(() => endCommand(token));
  }

  return (
    <div className="grid gap-6">
      <div
        className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]"
        inert={paying}
      >
        <section
          aria-busy={rows.pending}
          aria-labelledby="pt-commissions-list-title"
          className={cardClass}
        >
          <div className="border-b border-slate-200 px-4 py-4 sm:px-5">
            <h2
              className="text-sm font-bold tracking-wide text-slate-700 uppercase"
              id="pt-commissions-list-title"
            >
              {" "}
              {tx("Commission list")}{" "}
            </h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              {" "}
              {tx(
                "Status, commission, outstanding balance, trainer, and sale.",
              )}{" "}
            </p>
          </div>
          <div className="border-b border-slate-200 bg-slate-50/70 px-4 py-3 sm:px-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <label className="grid min-w-0 flex-1 gap-1 text-xs font-semibold tracking-wide text-slate-500 uppercase sm:max-w-xs">
                {" "}
                {tx("Commission status")}{" "}
                <SelectInput
                  onChange={(event) => setStatus(event.target.value)}
                  value={status}
                >
                  <option value="">{tx("All")}</option>
                  <option value="ACCRUED">{tx("Outstanding · accrued")}</option>
                  <option value="PARTIALLY_PAID">
                    {" "}
                    {tx("Outstanding · partially paid")}{" "}
                  </option>
                  <option value="PAID">{tx("PAID")}</option>
                  <option value="VOID">{tx("VOID")}</option>
                </SelectInput>
              </label>
              {filtered ? (
                <button
                  className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline sm:mb-2"
                  onClick={() => setStatus("")}
                  type="button"
                >
                  {" "}
                  {tx("Clear filter")}{" "}
                </button>
              ) : null}
            </div>
          </div>
          <div className="grid gap-4 p-4 sm:p-5">
            {rows.pending && rows.rows.length === 0 ? (
              <div className="grid gap-3" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading commissions.")}{" "}
                </p>
                <div className="grid gap-3">
                  {[0, 1, 2].map((item) => (
                    <div
                      className="h-24 animate-pulse rounded-xl bg-slate-100"
                      key={item}
                    />
                  ))}
                </div>
              </div>
            ) : null}
            {rows.pending && rows.rows.length > 0 ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Loading commissions.")}{" "}
              </p>
            ) : null}
            {!rows.pending && rows.error ? (
              <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="text-sm font-medium text-red-800" role="alert">
                  {rows.error}
                </p>
                <button
                  className="button w-fit"
                  onClick={() => rows.reload()}
                  type="button"
                >
                  {" "}
                  {tx("Retry loading commissions")}{" "}
                </button>
              </div>
            ) : null}
            {!rows.pending && !rows.error && rows.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                <p className="font-semibold text-slate-700">
                  {" "}
                  {tx("No commissions match.")}{" "}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  {filtered
                    ? tx("Try changing or clearing the current filter.")
                    : tx("Commissions appear here when a sale accrues one.")}
                </p>
              </div>
            ) : null}
            {rows.rows.length > 0 ? (
              <ul aria-label={tx("Commission list")} className="grid gap-3">
                {rows.rows.map((row) => {
                  const active = selected?.id === row.id;
                  return (
                    <li key={row.id}>
                      <button
                        aria-label={rowLabel(row, tx)}
                        aria-pressed={active}
                        className={`w-full rounded-xl border border-l-4 p-4 text-left ${
                          active
                            ? "border-l-teal-700 bg-teal-50 ring-2 ring-teal-600"
                            : "border-l-slate-300 bg-white hover:bg-slate-50"
                        } border-slate-200`}
                        onClick={() => void openRow(row.id)}
                        type="button"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-xs font-semibold tracking-wide text-slate-500">
                              {" "}
                              {tx("Outstanding")}{" "}
                            </p>
                            <p className="text-lg font-black text-slate-900 tabular-nums">
                              {row.outstanding_amount}
                              {row.currency ? (
                                <span className="text-xs font-semibold text-slate-500">{` ${tx(row.currency)}`}</span>
                              ) : null}
                            </p>
                          </div>
                          <div className="flex shrink-0 flex-col items-end gap-2">
                            {row.status ? (
                              <CommissionStatusBadge status={row.status} />
                            ) : null}
                            {active ? (
                              <span className="inline-flex rounded-full bg-teal-700 px-2.5 py-1 text-xs font-bold text-white">
                                {" "}
                                {tx("Selected")}{" "}
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3">
                          {row.commission_amount ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {" "}
                                {tx("Commission")}{" "}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900 tabular-nums">
                                {moneyLabel(
                                  row.commission_amount,
                                  row.currency,
                                )}
                              </dd>
                            </div>
                          ) : null}
                          {row.paid_amount ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {tx("Paid")}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900 tabular-nums">
                                {moneyLabel(row.paid_amount, row.currency)}
                              </dd>
                            </div>
                          ) : null}
                          {row.trainer_staff_profile_id ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {" "}
                                {tx("Trainer")}{" "}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900">
                                <IdentifierValue
                                  value={row.trainer_staff_profile_id}
                                />
                              </dd>
                            </div>
                          ) : null}
                          {row.pt_sale_id ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {tx("Sale")}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900">
                                <IdentifierValue value={row.pt_sale_id} />
                              </dd>
                            </div>
                          ) : null}
                        </dl>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
          {showPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={rows.bounded}
                hasMore={rows.hasMore}
                index={rows.index}
                onNext={() => void rows.goNext()}
                onPrevious={() => void rows.goPrevious()}
                pending={rows.pending}
              />
            </div>
          ) : null}
        </section>

        <CommissionDetail
          balanceCurrency={balanceCurrency}
          canPay={canPay}
          detailPending={detailPending}
          loadError={loadError}
          notice={notice}
          onOpenPayout={openPayout}
          onRetry={() => {
            if (requestedId) void openRow(requestedId);
          }}
          outstanding={outstanding}
          payButtonRef={payButtonRef}
          payerDiffers={payerDiffers}
          paying={paying}
          selected={selected}
          showPayout={showPayout}
        />
      </div>

      {paying && selected && canPay ? (
        <SaleCommandDialog
          description={tx(
            "Use the outstanding amount for a full payout, or a smaller amount for a partial payout. Currency {currency}.",
            { currency: tx(currency) },
          )}
          descriptionId="payout-description"
          dialogId="payout-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closePayout}
          onSubmit={savePayout}
          pending={commandPending}
          submitClass="button-edit"
          submitDisabled={sessionsBlocked}
          submitLabel="Save payout"
          title={tx("Record payout")}
          titleId="payout-title"
        >
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              {" "}
              {tx("Outstanding balance")}{" "}
            </p>
            <p className="mt-1 text-2xl font-black text-slate-900 tabular-nums">
              {selected.outstanding_amount}
              {balanceCurrency ? (
                <span className="text-sm font-semibold text-slate-500">{` ${balanceCurrency}`}</span>
              ) : null}
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Amount">
              <TextInput
                defaultValue={selected.outstanding_amount}
                inputMode="decimal"
                name="amount"
                required
              />
            </Field>
            <Field label="Business date">
              <TextInput
                name="business_date"
                onChange={(event) => setPayoutDate(event.target.value)}
                required
                type="date"
                value={payoutDate}
              />
            </Field>
            <div className="sm:col-span-2">
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
            </div>
            <div className="sm:col-span-2">
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
                <p className="text-sm text-slate-600">
                  {" "}
                  {tx(
                    "This method is not cash, so no cash session is sent.",
                  )}{" "}
                </p>
              )}
            </div>
            <Field label="Reference">
              <TextInput name="reference_number" />
            </Field>
            <Field label="Policy note">
              <TextInput name="policy_note" />
            </Field>
          </div>
        </SaleCommandDialog>
      ) : null}
    </div>
  );
}

function CommissionDetail({
  selected,
  balanceCurrency,
  notice,
  detailPending,
  loadError,
  canPay,
  outstanding,
  payerDiffers,
  showPayout,
  paying,
  payButtonRef,
  onOpenPayout,
  onRetry,
}: {
  selected: PTCommissionDetail | null;
  balanceCurrency: string;
  notice: string;
  detailPending: boolean;
  loadError: string;
  canPay: boolean;
  outstanding: boolean;
  payerDiffers: boolean;
  showPayout: boolean;
  paying: boolean;
  payButtonRef: RefObject<HTMLButtonElement | null>;
  onOpenPayout: () => void;
  onRetry: () => void;
}) {
  const tx = useTx();
  const payments = selected?.payments ?? [];
  return (
    <section
      aria-busy={detailPending}
      aria-labelledby="commission-detail-title"
      className={cardClass}
    >
      <div className="grid gap-5 p-4 sm:p-5">
        {selected ? (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2
                  className="text-xl font-bold text-slate-900"
                  id="commission-detail-title"
                >
                  {" "}
                  {tx("Commission detail")}{" "}
                </h2>
                {selected.commission_percent_snapshot &&
                selected.gross_fee_snapshot ? (
                  <p className="mt-1 text-sm text-slate-600">
                    {selected.commission_percent_snapshot}
                    {tx("% of")}{" "}
                    {moneyLabel(selected.gross_fee_snapshot, balanceCurrency)}
                  </p>
                ) : null}
              </div>
              {selected.status ? (
                <CommissionStatusBadge status={selected.status} />
              ) : null}
            </div>
            {notice ? <NoticeBanner message={notice} /> : null}
            {detailPending ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Loading commission.")}{" "}
              </p>
            ) : null}
            {loadError ? (
              <div className="grid gap-3">
                <Alert message={loadError} />
                <button
                  className="button w-fit"
                  onClick={onRetry}
                  type="button"
                >
                  {" "}
                  {tx("Retry loading commission")}{" "}
                </button>
              </div>
            ) : null}
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {" "}
                {tx("Financial summary")}{" "}
              </h3>
              <dl className="mt-3 grid grid-cols-2 gap-3">
                <Fact
                  emphasis
                  label="Commission"
                  value={moneyLabel(
                    selected.commission_amount,
                    balanceCurrency,
                  )}
                />
                <Fact
                  emphasis
                  label="Outstanding"
                  value={moneyLabel(
                    selected.outstanding_amount,
                    balanceCurrency,
                  )}
                />
                <Fact
                  label="Paid"
                  value={moneyLabel(selected.paid_amount, balanceCurrency)}
                />
                <Fact
                  label="Gym share"
                  value={moneyLabel(selected.gym_share, balanceCurrency)}
                />
              </dl>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Identities")}
              </h3>
              <dl className="mt-3 grid grid-cols-2 gap-3">
                <Fact
                  label="Trainer"
                  value={selected.trainer_staff_profile_id}
                />
                <Fact label="Sale" value={selected.pt_sale_id} />
                <Fact label="Commission" value={selected.id} />
                {selected.created_by_membership_id ? (
                  <Fact
                    label="Recorded by"
                    value={selected.created_by_membership_id}
                  />
                ) : null}
                {selected.paid_at ? (
                  <Fact label="Paid at" value={selected.paid_at} />
                ) : null}
              </dl>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <h3 className="text-sm font-bold text-slate-800">
                {" "}
                {tx("Payout history")}{" "}
              </h3>
              {payments.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">
                  {" "}
                  {tx("No payouts have been recorded.")}{" "}
                </p>
              ) : (
                <ol
                  aria-label={tx("Payout history")}
                  className="mt-3 border-l border-slate-200"
                >
                  {payments.map((payment) => (
                    <PayoutEntry key={payment.id} payment={payment} />
                  ))}
                </ol>
              )}
            </div>
            {showPayout ? (
              <div
                aria-label={tx("Commission actions")}
                className="border-t border-slate-100 pt-4"
                role="group"
              >
                <button
                  aria-controls={paying ? "payout-dialog" : undefined}
                  aria-expanded={paying}
                  className="button button-edit"
                  disabled={detailPending}
                  onClick={onOpenPayout}
                  ref={payButtonRef}
                  type="button"
                >
                  {" "}
                  {tx("Record payout")}{" "}
                </button>
              </div>
            ) : null}
            {canPay && outstanding && !payerDiffers ? (
              <p className="border-t border-slate-100 pt-4 text-sm text-slate-600">
                {" "}
                {tx("A different person must record this payout.")}{" "}
              </p>
            ) : null}
          </>
        ) : (
          <>
            <h2
              className="text-sm font-bold tracking-wide text-slate-700 uppercase"
              id="commission-detail-title"
            >
              {" "}
              {tx("Commission detail")}{" "}
            </h2>
            {detailPending ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Loading commission.")}{" "}
              </p>
            ) : null}
            {loadError ? (
              <div className="grid gap-3">
                <Alert message={loadError} />
                <button
                  className="button w-fit"
                  onClick={onRetry}
                  type="button"
                >
                  {" "}
                  {tx("Retry loading commission")}{" "}
                </button>
              </div>
            ) : !detailPending ? (
              <p className="text-sm text-slate-500">
                {" "}
                {tx("Select a commission to see its balance and payouts.")}{" "}
              </p>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}

function PayoutEntry({
  payment,
}: {
  payment: PTCommissionDetail["payments"][number];
}) {
  const tx = useTx();
  return (
    <li className="relative py-3 pl-4">
      <span
        aria-hidden="true"
        className="absolute top-5 -left-1 size-2 rounded-full border-2 border-white bg-slate-400"
      />
      <div className="flex items-start justify-between gap-3">
        <p className="font-semibold text-slate-900 tabular-nums">
          {payment.amount} {tx(payment.payment_method)}
        </p>
        {payment.status ? (
          <CommissionStatusBadge status={payment.status} />
        ) : null}
      </div>
      {payment.business_date ? (
        <p className="mt-1 text-sm text-slate-600">
          <time dateTime={payment.business_date}>
            {formatAppDate(payment.business_date)}
          </time>
        </p>
      ) : null}
      {payment.reference_number ? (
        <p className="mt-1 text-sm text-slate-600">
          {" "}
          {tx("Reference")} {payment.reference_number}
        </p>
      ) : null}
      {payment.cash_session_id ? (
        <p className="mt-1 text-sm text-slate-600">
          {" "}
          {tx("Cash session")}{" "}
          <IdentifierValue value={payment.cash_session_id} />
        </p>
      ) : null}
    </li>
  );
}

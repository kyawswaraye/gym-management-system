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
  ReasonField,
  SelectInput,
  ServerFacts,
  StatusText,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import {
  approvePtSale,
  cancelPtSale,
  createPtSale,
  getPtCommission,
  getPtSale,
  listPtCommissions,
  listPtSales,
  listServicePackages,
  listTrainers,
  rejectPtSale,
  submitPtSale,
  updatePtSale,
  voidPtSale,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { collectLookupPages, useLookupOptions } from "@/features/pt/lookup";
import { MemberSearch, type PickedMember } from "@/features/pt/member-search";
import type {
  PTCommissionDetail,
  PTSale,
  PTSalePaymentDraftRequest,
  PTSaleWriteRequest,
  ServicePackage,
  TrainerIdentity,
} from "@/types/api";

const PAGE = "25";
const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function SalesManager({
  canCreate,
  canUpdate,
  canSubmit,
  canApprove,
  canVoid,
  currency,
  today,
  membershipId,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  currency: string;
  today: string;
  membershipId: string;
}) {
  const [status, setStatus] = useState("");
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [selected, setSelected] = useState<PTSale | null>(null);
  const [commission, setCommission] = useState<PTCommissionDetail | null>(null);
  const [commissionUnresolved, setCommissionUnresolved] = useState(false);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [voidDate, setVoidDate] = useState(today);
  const [mode, setMode] = useState<
    "create" | "edit" | "reject" | "cancel" | "approve" | "void" | null
  >(null);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const sales = useCursorPages<PTSale>(
    `sales:${status}:${memberFilter?.id ?? ""}`,
    (after) =>
      listPtSales({
        limit: PAGE,
        ...(after ? { after } : {}),
        ...(status ? { status } : {}),
        ...(memberFilter ? { member_id: memberFilter.id } : {}),
      }),
  );
  const trainers = useLookupOptions<TrainerIdentity>(
    "sale-trainers",
    (after) => listTrainers({ limit: "100", ...(after ? { after } : {}) }),
    mode === "create" || mode === "edit",
  );
  const packages = useLookupOptions<ServicePackage>(
    "sale-packages",
    (after) =>
      listServicePackages({
        limit: "100",
        type: "PT",
        is_active: "true",
        ...(after ? { after } : {}),
      }),
    mode === "create" || mode === "edit",
  );
  const cashPayment = selected?.payment?.method === "CASH";
  const postedCashPayouts = (commission?.payments ?? []).filter(
    (row) => row.status === "POSTED" && row.payment_method === "CASH",
  );
  const approvalDate =
    mode === "approve" && cashPayment
      ? (selected?.payment?.business_date ?? "")
      : "";
  const voidNeedsSession =
    mode === "void" && (cashPayment || postedCashPayouts.length > 0);
  const sessionDate = approvalDate || (voidNeedsSession ? voidDate : "");
  const sessions = useOpenCashSessions(sessionDate);
  const sessionsBlocked =
    sessions.pending || Boolean(sessions.error) || sessionDate === "";

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  async function openSale(saleId: string) {
    setPending(true);
    setError("");
    try {
      const sale = await getPtSale(saleId);
      setSelected(sale);
      setCommission(null);
      setCommissionUnresolved(false);
      if (sale.status === "POSTED" || sale.status === "VOID") {
        const collected = await collectLookupPages((after) =>
          listPtCommissions({
            limit: "100",
            trainer_staff_profile_id: sale.trainer_staff_profile_id,
            service_date_from: sale.service_date,
            service_date_to: sale.service_date,
            ...(after ? { after } : {}),
          }),
        );
        const match = collected.items.find((row) => row.pt_sale_id === sale.id);
        if (match) {
          setCommission(await getPtCommission(match.id));
        } else if (collected.error || collected.bounded) {
          setCommissionUnresolved(true);
          setError(
            collected.error ||
              "The commission list is incomplete. Refine the trainer and date, then open the sale again.",
          );
        }
      }
    } catch (caught) {
      setError(phase5Error(caught, "The sale could not be loaded."));
    } finally {
      setPending(false);
    }
  }

  function paymentDraft(
    form: FormData,
  ): PTSalePaymentDraftRequest | null | undefined {
    const choice = text(form, "payment_choice");
    if (choice === "omit") return undefined;
    if (choice === "clear") return null;
    return {
      description: text(form, "payment_description"),
      amount: text(form, "payment_amount"),
      method: text(
        form,
        "payment_method",
      ) as PTSalePaymentDraftRequest["method"],
      business_date: text(form, "payment_business_date"),
      ...(text(form, "reference_number")
        ? { reference_number: text(form, "reference_number") }
        : {}),
    };
  }

  function saleBody(form: FormData): PTSaleWriteRequest {
    const packageId = text(form, "service_package_id");
    const payment = paymentDraft(form);
    return {
      member_id: member?.id ?? "",
      trainer_staff_profile_id: text(form, "trainer_staff_profile_id"),
      service_date: text(form, "service_date"),
      description: text(form, "description"),
      gross_fee: text(form, "gross_fee"),
      currency,
      ...(packageId
        ? { service_package_id: packageId }
        : { service_package_id: null }),
      ...(payment !== undefined ? { payment } : {}),
    };
  }

  const separateActor =
    selected !== null &&
    selected.created_by_membership_id !== membershipId &&
    selected.submitted_by_membership_id !== membershipId;

  return (
    <div className="grid gap-4">
      <Alert message={error || sales.error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <MemberSearch
        action="Search sale members"
        label="Filter sales by member"
        onSelect={setMemberFilter}
        selected={memberFilter}
      />
      {memberFilter ? (
        <button
          className="underline"
          onClick={() => setMemberFilter(null)}
          type="button"
        >
          Clear member filter
        </button>
      ) : null}
      <Field label="Sale status">
        <SelectInput
          onChange={(event) => setStatus(event.target.value)}
          value={status}
        >
          <option value="">All</option>
          {[
            "DRAFT",
            "PENDING_APPROVAL",
            "POSTED",
            "REJECTED",
            "CANCELLED",
            "VOID",
          ].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </SelectInput>
      </Field>
      {sales.pending || pending ? <Loading label="Loading sales." /> : null}
      {!sales.pending && sales.rows.length === 0 ? (
        <Empty label="No PT sales match." />
      ) : (
        <ul className="grid gap-3">
          {sales.rows.map((row) => (
            <li key={row.id}>
              <button
                className="underline"
                onClick={() => void openSale(row.id)}
                type="button"
              >
                {row.service_date} · {row.gross_fee} {row.currency} · member{" "}
                {row.member_id} · trainer {row.trainer_staff_profile_id}
              </button>{" "}
              <StatusText status={row.status} />
            </li>
          ))}
        </ul>
      )}
      <CursorControls
        bounded={sales.bounded}
        hasMore={sales.hasMore}
        index={sales.index}
        onNext={() => void sales.goNext()}
        onPrevious={() => void sales.goPrevious()}
        pending={sales.pending}
      />
      {canCreate ? (
        <button
          className="button"
          onClick={() => setMode("create")}
          type="button"
        >
          Create draft
        </button>
      ) : null}
      {selected ? (
        <section aria-labelledby="sale-detail-title" className="grid gap-3">
          <h2 className="text-xl font-semibold" id="sale-detail-title">
            Sale detail
          </h2>
          <ServerFacts
            rows={[
              { label: "Status", value: selected.status },
              {
                label: "Gross fee",
                value: `${selected.gross_fee} ${selected.currency}`,
              },
              { label: "Commission", value: selected.commission_amount },
              { label: "Gym share", value: selected.gym_share },
              { label: "Outstanding", value: selected.outstanding_amount },
              {
                label: "Package",
                value: selected.service_package_id ?? "None",
              },
              { label: "Payment", value: selected.payment?.method ?? "None" },
            ]}
          />
          {selected.status === "DRAFT" && canUpdate ? (
            <div className="flex gap-3">
              <button
                className="underline"
                onClick={() => setMode("edit")}
                type="button"
              >
                Edit draft
              </button>
              <button
                className="underline"
                onClick={() => setMode("cancel")}
                type="button"
              >
                Cancel draft
              </button>
            </div>
          ) : null}
          {selected.status === "DRAFT" && canSubmit ? (
            <button
              className="button"
              disabled={pending}
              onClick={() => {
                const fingerprint = `submit:${selected.id}`;
                setPending(true);
                setError("");
                void submitPtSale(selected.id, commandKey(fingerprint))
                  .then(async () => {
                    key.current = null;
                    setNotice("Sale submitted.");
                    await openSale(selected.id);
                    sales.reload();
                  })
                  .catch((caught: unknown) => {
                    setError(
                      phase5Error(caught, "The sale could not be submitted."),
                    );
                  })
                  .finally(() => setPending(false));
              }}
              type="button"
            >
              Submit sale
            </button>
          ) : null}
          {selected.status === "PENDING_APPROVAL" &&
          canApprove &&
          separateActor ? (
            <div className="flex gap-3">
              <button
                className="button"
                onClick={() => setMode("approve")}
                type="button"
              >
                Approve sale
              </button>
              <button
                className="underline"
                onClick={() => setMode("reject")}
                type="button"
              >
                Reject sale
              </button>
            </div>
          ) : null}
          {selected.status === "POSTED" && canVoid ? (
            <button
              className="button"
              onClick={() => {
                setVoidDate(today);
                setMode("void");
              }}
              type="button"
            >
              Void sale
            </button>
          ) : null}
        </section>
      ) : null}
      {(mode === "create" && canCreate) ||
      (mode === "edit" && canUpdate && selected) ? (
        <form
          aria-labelledby="sale-form-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!member && mode === "create") {
              setError("Select an active member.");
              return;
            }
            const form = new FormData(event.currentTarget);
            const body = saleBody(form);
            if (mode === "edit" && selected && !member)
              body.member_id = selected.member_id;
            const fingerprint = JSON.stringify(body);
            setPending(true);
            setError("");
            const request =
              mode === "edit" && selected
                ? updatePtSale(selected.id, body, commandKey(fingerprint))
                : createPtSale(body, commandKey(fingerprint));
            void request
              .then(async (sale) => {
                key.current = null;
                setNotice(
                  mode === "edit" ? "Draft updated." : "Draft created.",
                );
                setMode(null);
                setSelected(sale);
                sales.reload();
              })
              .catch((caught: unknown) => {
                setError(phase5Error(caught, "The draft could not be saved."));
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="sale-form-title">
            {mode === "edit" ? "Edit draft" : "Create draft"}
          </h3>
          <MemberSearch
            disabled={pending}
            onSelect={setMember}
            selected={member}
          />
          <Alert message={trainers.error || packages.error} />
          <Field label="Trainer">
            <SelectInput
              defaultValue={selected?.trainer_staff_profile_id ?? ""}
              name="trainer_staff_profile_id"
              required
            >
              <option value="">Select a trainer</option>
              {selected?.trainer_staff_profile_id &&
              !trainers.items.some(
                (trainer) => trainer.id === selected.trainer_staff_profile_id,
              ) ? (
                <option value={selected.trainer_staff_profile_id}>
                  Current trainer
                </option>
              ) : null}
              {trainers.items.map((trainer) => (
                <option key={trainer.id} value={trainer.id}>
                  {trainer.display_name} ({trainer.staff_code})
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Service date">
            <TextInput
              defaultValue={selected?.service_date ?? today}
              name="service_date"
              required
              type="date"
            />
          </Field>
          <Field label="Description">
            <TextInput
              defaultValue={selected?.description ?? ""}
              name="description"
              required
            />
          </Field>
          <Field label="Gross fee">
            <TextInput
              defaultValue={selected?.gross_fee ?? ""}
              name="gross_fee"
              required
            />
          </Field>
          <Field label="PT package">
            <SelectInput
              defaultValue={selected?.service_package_id ?? ""}
              name="service_package_id"
            >
              <option value="">None</option>
              {packages.items.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.code} · {row.name} · {row.price}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Payment draft">
            <SelectInput defaultValue="omit" name="payment_choice">
              <option value="omit">Leave unchanged or omit</option>
              <option value="set">Set payment draft</option>
              <option value="clear">Remove payment draft</option>
            </SelectInput>
          </Field>
          <Field label="Payment description">
            <TextInput name="payment_description" />
          </Field>
          <Field label="Payment amount">
            <TextInput name="payment_amount" />
          </Field>
          <Field label="Payment method">
            <SelectInput defaultValue="CASH" name="payment_method">
              {METHODS.map((method) => (
                <option key={method} value={method}>
                  {method}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Payment business date">
            <TextInput
              defaultValue={today}
              name="payment_business_date"
              type="date"
            />
          </Field>
          <Field label="Payment reference">
            <TextInput name="reference_number" />
          </Field>
          <button className="button" disabled={pending} type="submit">
            Save draft
          </button>
        </form>
      ) : null}
      {mode === "approve" && selected && canApprove ? (
        <form
          aria-labelledby="approve-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const cashSessionId = text(form, "cash_session_id");
            if (cashPayment && (sessions.error || !cashSessionId)) {
              setError(sessions.error || "Select an open cash session.");
              return;
            }
            const body = cashPayment ? { cash_session_id: cashSessionId } : {};
            setPending(true);
            setError("");
            void approvePtSale(
              selected.id,
              body,
              commandKey(`approve:${selected.id}:${cashSessionId}`),
            )
              .then(async () => {
                key.current = null;
                setNotice("Sale approved.");
                setMode(null);
                await openSale(selected.id);
                sales.reload();
              })
              .catch((caught: unknown) => {
                setError(
                  phase5Error(caught, "The sale could not be approved."),
                );
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="approve-title">
            Approve sale
          </h3>
          {cashPayment ? (
            <>
              <p className="muted">
                {selected.payment?.business_date
                  ? `Open sessions are listed for the payment business date ${selected.payment.business_date}.`
                  : "This cash payment has no business date, so a session cannot be chosen."}
              </p>
              <CashSessionSelect
                collectionError={sessions.error}
                currency={currency}
                label="Cash session for the payment"
                pending={sessions.pending}
                resetKey={approvalDate}
                sessions={sessions.sessions}
              />
            </>
          ) : (
            <p className="muted">
              This sale has no cash payment, so no cash session is sent.
            </p>
          )}
          <button
            className="button"
            disabled={pending || (cashPayment && sessionsBlocked)}
            type="submit"
          >
            Confirm approval
          </button>
        </form>
      ) : null}
      {mode === "reject" && selected && canApprove ? (
        <form
          aria-labelledby="reject-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = text(
              new FormData(event.currentTarget),
              "rejection_reason",
            );
            setPending(true);
            void rejectPtSale(
              selected.id,
              { rejection_reason: reason },
              commandKey(`reject:${selected.id}:${reason}`),
            )
              .then(async () => {
                key.current = null;
                setNotice("Sale rejected.");
                setMode(null);
                await openSale(selected.id);
                sales.reload();
              })
              .catch((caught: unknown) => {
                setError(
                  phase5Error(caught, "The sale could not be rejected."),
                );
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="reject-title">
            Reject sale
          </h3>
          <ReasonField label="Rejection reason" name="rejection_reason" />
          <button className="button" disabled={pending} type="submit">
            Confirm rejection
          </button>
        </form>
      ) : null}
      {mode === "cancel" && selected && canUpdate ? (
        <form
          aria-labelledby="cancel-sale-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = text(new FormData(event.currentTarget), "reason");
            setPending(true);
            void cancelPtSale(
              selected.id,
              { reason },
              commandKey(`cancel:${selected.id}:${reason}`),
            )
              .then(async () => {
                key.current = null;
                setNotice("Draft cancelled.");
                setMode(null);
                await openSale(selected.id);
                sales.reload();
              })
              .catch((caught: unknown) => {
                setError(
                  phase5Error(caught, "The draft could not be cancelled."),
                );
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="cancel-sale-title">
            Cancel draft
          </h3>
          <ReasonField label="Cancellation reason" />
          <button className="button" disabled={pending} type="submit">
            Confirm cancellation
          </button>
        </form>
      ) : null}
      {mode === "void" && selected && canVoid ? (
        <form
          aria-labelledby="void-sale-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (commissionUnresolved) {
              setError(
                "The sale commission could not be loaded completely. Payout destinations are not listed, so this void was not sent.",
              );
              return;
            }
            if (voidNeedsSession && (sessions.error || !voidDate)) {
              setError(
                sessions.error ||
                  "Choose a business date with a complete open-session list.",
              );
              return;
            }
            const form = new FormData(event.currentTarget);
            const payouts = (commission?.payments ?? []).filter(
              (row) => row.status === "POSTED",
            );
            if (cashPayment && !text(form, "cash_session_id")) {
              setError("Select an open cash session.");
              return;
            }
            if (
              payouts.some(
                (row) =>
                  row.payment_method === "CASH" &&
                  !text(form, `payout_${row.id}`),
              )
            ) {
              setError("Select an open session for every cash payout.");
              return;
            }
            const body = {
              reason: text(form, "reason"),
              business_date: text(form, "business_date"),
              ...(cashPayment
                ? { cash_session_id: text(form, "cash_session_id") }
                : {}),
              payout_reversals: payouts.map((row) => ({
                commission_payment_id: row.id,
                cash_session_id:
                  row.payment_method === "CASH"
                    ? text(form, `payout_${row.id}`) || null
                    : null,
              })),
            };
            setPending(true);
            void voidPtSale(selected.id, body, commandKey(JSON.stringify(body)))
              .then(async () => {
                key.current = null;
                setNotice("Sale voided.");
                setMode(null);
                await openSale(selected.id);
                sales.reload();
              })
              .catch((caught: unknown) => {
                setError(phase5Error(caught, "The sale could not be voided."));
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="void-sale-title">
            Void sale
          </h3>
          {commissionUnresolved ? (
            <p className="error" role="alert">
              The sale commission could not be loaded completely. Payout
              destinations are not listed, so this void was not sent.
            </p>
          ) : null}
          <ReasonField label="Void reason" />
          <Field label="Business date">
            <TextInput
              name="business_date"
              onChange={(event) => setVoidDate(event.target.value)}
              required
              type="date"
              value={voidDate}
            />
          </Field>
          {cashPayment ? (
            <CashSessionSelect
              collectionError={sessions.error}
              currency={currency}
              label="Cash session for the payment reversal"
              pending={sessions.pending}
              resetKey={voidDate}
              sessions={sessions.sessions}
            />
          ) : null}
          {postedCashPayouts.map((row) => (
            <CashSessionSelect
              collectionError={sessions.error}
              currency={currency}
              key={row.id}
              label={`Payout destination for ${row.amount}`}
              name={`payout_${row.id}`}
              pending={sessions.pending}
              resetKey={voidDate}
              sessions={sessions.sessions}
            />
          ))}
          <p className="muted">
            Every posted payout is reversed. A consumed package returns
            PACKAGE_CONSUMED and changes nothing.
          </p>
          <button
            className="button"
            disabled={
              pending ||
              commissionUnresolved ||
              (voidNeedsSession && sessionsBlocked)
            }
            type="submit"
          >
            Confirm void
          </button>
        </form>
      ) : null}
    </div>
  );
}

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
import { listReceipts } from "@/features/finance/client";
import { ReceiptPrintLink } from "@/features/finance/receipt-preview";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import { formatAppDate } from "@/lib/date";
import { SaleCommandDialog } from "@/features/pt/sale-command-dialog";
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
import { IdentifierValue } from "@/lib/ui/identifier";
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
const SALE_STATUSES = [
  "DRAFT",
  "PENDING_APPROVAL",
  "POSTED",
  "REJECTED",
  "CANCELLED",
  "VOID",
] as const;

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

type SaleMode = "create" | "edit" | "reject" | "cancel" | "approve" | "void";

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function saleStatusClass(status: string): string {
  switch (status) {
    case "DRAFT":
      return "bg-slate-200 text-slate-800";
    case "PENDING_APPROVAL":
      return "bg-amber-100 text-amber-900";
    case "POSTED":
      return "bg-emerald-100 text-emerald-800";
    case "REJECTED":
    case "VOID":
      return "bg-red-100 text-red-800";
    case "CANCELLED":
      return "bg-slate-100 text-slate-600";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

function saleLabel(
  row: PTSale,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  const packagePart = row.service_package_id
    ? ` ${tx("package")} ${row.service_package_id}`
    : "";
  const paymentPart = row.payment?.method
    ? ` ${tx("payment")} ${tx(row.payment.method)}`
    : "";
  return `${formatAppDate(row.service_date)} ${row.description} ${row.gross_fee} ${tx(row.currency)} ${tx("member")} ${row.member_id} ${tx("trainer")} ${row.trainer_staff_profile_id}${packagePart}${paymentPart} ${tx(row.status)}`;
}

function SaleStatusBadge({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-bold ${saleStatusClass(status)}`}
    >
      {tx(status)}
    </span>
  );
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
    <div>
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

export function SalesManager({
  canCreate,
  canUpdate,
  canSubmit,
  canApprove,
  canVoid,
  canViewReceipts = false,
  currency,
  today,
  membershipId,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  canViewReceipts?: boolean;
  currency: string;
  today: string;
  membershipId: string;
}) {
  const tx = useTx();
  const [status, setStatus] = useState("");
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [selected, setSelected] = useState<PTSale | null>(null);
  const [commission, setCommission] = useState<PTCommissionDetail | null>(null);
  const [commissionUnresolved, setCommissionUnresolved] = useState(false);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [notice, setNotice] = useState("");
  const [saleReceiptId, setSaleReceiptId] = useState<string | null>(null);
  const [actionReceiptIds, setActionReceiptIds] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [detailPending, setDetailPending] = useState(false);
  const [commandPending, setCommandPending] = useState(false);
  const [voidDate, setVoidDate] = useState(today);
  const [mode, setMode] = useState<SaleMode | null>(null);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const pendingRef = useRef(false);
  const commandToken = useRef(0);
  const detailToken = useRef(0);
  const [requestedSaleId, setRequestedSaleId] = useState("");
  const dialogRef = useRef<HTMLFormElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const approveButtonRef = useRef<HTMLButtonElement>(null);
  const rejectButtonRef = useRef<HTMLButtonElement>(null);
  const voidButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<SaleMode | null>(null);
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
  const filtered = status !== "" || memberFilter !== null;
  const showPaging = sales.index > 0 || sales.hasMore || sales.bounded;
  const separateActor =
    selected !== null &&
    selected.created_by_membership_id !== membershipId &&
    selected.submitted_by_membership_id !== membershipId;

  useEffect(() => {
    if (!canViewReceipts) return undefined;
    const paymentId =
      selected?.status === "POSTED" ? selected.payment_id : null;
    if (!paymentId) {
      setSaleReceiptId((current) => (current === null ? current : null));
      return undefined;
    }
    let active = true;
    setSaleReceiptId(null);
    void listReceipts({ payment: paymentId, status: "ISSUED", limit: "5" })
      .then((page) => {
        if (!active) return;
        const match = page.data.find(
          (row) =>
            row.payment_id.toLowerCase() === paymentId.toLowerCase() &&
            row.status === "ISSUED",
        );
        setSaleReceiptId(match?.id ?? null);
      })
      .catch(() => {
        if (active) setSaleReceiptId(null);
      });
    return () => {
      active = false;
    };
  }, [
    canViewReceipts,
    selected?.id,
    selected?.payment_id,
    selected?.status,
  ]);

  useEffect(() => {
    if (mode) {
      restoreFocus.current = mode;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    const previous = restoreFocus.current;
    if (!previous) return;
    restoreFocus.current = null;
    const targets: Record<SaleMode, RefObject<HTMLButtonElement | null>> = {
      create: createButtonRef,
      edit: editButtonRef,
      cancel: cancelButtonRef,
      approve: approveButtonRef,
      reject: rejectButtonRef,
      void: voidButtonRef,
    };
    targets[previous].current?.focus();
  }, [mode]);

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

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  function closeMode() {
    if (pendingRef.current) return;
    setDialogError("");
    setMode(null);
  }

  function openMode(next: SaleMode) {
    if (pendingRef.current || mode !== null) return;
    setDialogError("");
    if (next === "void") setVoidDate(today);
    setMode(next);
  }

  async function openSale(saleId: string) {
    const token = ++detailToken.current;
    if (canViewReceipts && saleId !== selected?.id) {
      setActionReceiptIds((current) => (current.length === 0 ? current : []));
    }
    setDetailPending(true);
    setLoadError("");
    setActionError("");
    setRequestedSaleId(saleId);
    try {
      const sale = await getPtSale(saleId);
      if (detailToken.current !== token) return;
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
        if (detailToken.current !== token) return;
        const match = collected.items.find((row) => row.pt_sale_id === sale.id);
        if (match) {
          const detail = await getPtCommission(match.id);
          if (detailToken.current !== token) return;
          setCommission(detail);
        } else if (collected.error || collected.bounded) {
          setCommissionUnresolved(true);
          setLoadError(
            collected.error ||
              "The commission list is incomplete. Refine the trainer and date, then open the sale again.",
          );
        }
      }
    } catch (caught) {
      if (detailToken.current !== token) return;
      setLoadError(phase5Error(caught, tx("The sale could not be loaded.")));
    } finally {
      if (detailToken.current === token) setDetailPending(false);
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

  function saveDraft(event: FormEvent<HTMLFormElement>) {
    if (pendingRef.current) return;
    if (!member && mode === "create") {
      setDialogError("Select an active member.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const body = saleBody(form);
    if (mode === "edit" && selected && !member)
      body.member_id = selected.member_id;
    if (!body.trainer_staff_profile_id) {
      setDialogError("Select a trainer.");
      return;
    }
    if (!body.service_date || !body.description || !body.gross_fee) {
      setDialogError("Enter the service date, description, and gross fee.");
      return;
    }
    const fingerprint = JSON.stringify(body);
    const token = beginCommand();
    setDialogError("");
    const request =
      mode === "edit" && selected
        ? updatePtSale(selected.id, body, commandKey(fingerprint))
        : createPtSale(body, commandKey(fingerprint));
    void request
      .then(async (sale) => {
        key.current = null;
        setNotice(mode === "edit" ? "Draft updated." : "Draft created.");
        setDialogError("");
        setLoadError("");
        setActionError("");
        setMode(null);
        setSelected(sale);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setDialogError(
          phase5Error(caught, tx("The draft could not be saved.")),
        );
      })
      .finally(() => endCommand(token));
  }

  function submitSale() {
    if (!selected || pendingRef.current) return;
    const fingerprint = `submit:${selected.id}`;
    const token = beginCommand();
    setActionError("");
    setNotice("");
    void submitPtSale(selected.id, commandKey(fingerprint))
      .then(async () => {
        key.current = null;
        setNotice(tx("Sale submitted."));
        await openSale(selected.id);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setActionError(
          phase5Error(caught, tx("The sale could not be submitted.")),
        );
      })
      .finally(() => endCommand(token));
  }

  function approveSale(event: FormEvent<HTMLFormElement>) {
    if (!selected || pendingRef.current) return;
    const form = new FormData(event.currentTarget);
    const cashSessionId = text(form, "cash_session_id");
    if (cashPayment && (sessions.error || !cashSessionId)) {
      setDialogError(sessions.error || "Select an open cash session.");
      return;
    }
    const body = cashPayment ? { cash_session_id: cashSessionId } : {};
    const token = beginCommand();
    setDialogError("");
    void approvePtSale(
      selected.id,
      body,
      commandKey(`approve:${selected.id}:${cashSessionId}`),
    )
      .then(async (result) => {
        key.current = null;
        if (canViewReceipts && result.receipt?.status === "ISSUED") {
          setActionReceiptIds([result.receipt.id]);
        }
        setNotice(tx("Sale approved."));
        setDialogError("");
        setMode(null);
        await openSale(selected.id);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setDialogError(
          phase5Error(caught, tx("The sale could not be approved.")),
        );
      })
      .finally(() => endCommand(token));
  }

  function rejectSale(event: FormEvent<HTMLFormElement>) {
    if (!selected || pendingRef.current) return;
    const reason = text(new FormData(event.currentTarget), "rejection_reason");
    if (!reason) {
      setDialogError("Enter a rejection reason.");
      return;
    }
    const token = beginCommand();
    setDialogError("");
    void rejectPtSale(
      selected.id,
      { rejection_reason: reason },
      commandKey(`reject:${selected.id}:${reason}`),
    )
      .then(async () => {
        key.current = null;
        setNotice(tx("Sale rejected."));
        setDialogError("");
        setMode(null);
        await openSale(selected.id);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setDialogError(
          phase5Error(caught, tx("The sale could not be rejected.")),
        );
      })
      .finally(() => endCommand(token));
  }

  function cancelSale(event: FormEvent<HTMLFormElement>) {
    if (!selected || pendingRef.current) return;
    const reason = text(new FormData(event.currentTarget), "reason");
    if (!reason) {
      setDialogError("Enter a cancellation reason.");
      return;
    }
    const token = beginCommand();
    setDialogError("");
    void cancelPtSale(
      selected.id,
      { reason },
      commandKey(`cancel:${selected.id}:${reason}`),
    )
      .then(async () => {
        key.current = null;
        setNotice(tx("Draft cancelled."));
        setDialogError("");
        setMode(null);
        await openSale(selected.id);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setDialogError(
          phase5Error(caught, tx("The draft could not be cancelled.")),
        );
      })
      .finally(() => endCommand(token));
  }

  function voidSale(event: FormEvent<HTMLFormElement>) {
    if (!selected || pendingRef.current) return;
    if (commissionUnresolved) {
      setDialogError(
        "The sale commission could not be loaded completely. Payout destinations are not listed, so this void was not sent.",
      );
      return;
    }
    if (voidNeedsSession && (sessions.error || !voidDate)) {
      setDialogError(
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
      setDialogError("Select an open cash session.");
      return;
    }
    if (
      payouts.some(
        (row) =>
          row.payment_method === "CASH" && !text(form, `payout_${row.id}`),
      )
    ) {
      setDialogError("Select an open session for every cash payout.");
      return;
    }
    const reason = text(form, "reason");
    const businessDate = text(form, "business_date");
    if (!reason || !businessDate) {
      setDialogError("Enter a void reason and business date.");
      return;
    }
    const body = {
      reason,
      business_date: businessDate,
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
    const token = beginCommand();
    setDialogError("");
    void voidPtSale(selected.id, body, commandKey(JSON.stringify(body)))
      .then(async (result) => {
        key.current = null;
        if (canViewReceipts) {
          const issued = [
            result.payment?.correction_receipt,
            result.payment?.original_receipt,
          ].flatMap((receipt) =>
            receipt?.status === "ISSUED" ? [receipt.id] : [],
          );
          setActionReceiptIds(issued);
        }
        setNotice(tx("Sale voided."));
        setDialogError("");
        setMode(null);
        await openSale(selected.id);
        sales.reload();
      })
      .catch((caught: unknown) => {
        setDialogError(
          phase5Error(caught, tx("The sale could not be voided.")),
        );
      })
      .finally(() => endCommand(token));
  }

  const draftOpen =
    (mode === "create" && canCreate) ||
    (mode === "edit" && canUpdate && selected !== null);
  const detailNotice = notice && selected ? notice : "";
  const printableReceiptIds = canViewReceipts
    ? [
        ...new Set(
          [saleReceiptId, ...actionReceiptIds].filter((id): id is string =>
            Boolean(id),
          ),
        ),
      ]
    : [];

  return (
    <div className="grid gap-6">
      <div
        className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]"
        inert={mode !== null}
      >
        {notice && !selected ? <NoticeBanner message={notice} /> : null}
        <section
          aria-busy={sales.pending}
          aria-labelledby="pt-sales-list-title"
          className={cardClass}
        >
          <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div>
              <h2
                className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                id="pt-sales-list-title"
              >
                {" "}
                {tx("Sales")}{" "}
              </h2>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                {" "}
                {tx("Service date, fee, member, trainer, and status.")}{" "}
              </p>
            </div>
            {canCreate ? (
              <button
                aria-controls={
                  draftOpen && mode === "create"
                    ? "sale-draft-dialog"
                    : undefined
                }
                aria-expanded={mode === "create"}
                className="button button-create w-full sm:w-auto"
                onClick={() => openMode("create")}
                ref={createButtonRef}
                type="button"
              >
                <span aria-hidden="true">+</span> {tx("Create draft")}{" "}
              </button>
            ) : null}
          </div>

          <div className="border-b border-slate-200 bg-slate-50/70 px-4 py-3 sm:px-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="min-w-0 flex-1">
                <MemberSearch
                  action="Search sale members"
                  label="Filter sales by member"
                  onSelect={setMemberFilter}
                  selected={memberFilter}
                />
              </div>
              <label className="grid gap-1 text-xs font-semibold tracking-wide text-slate-500 uppercase sm:w-48 lg:mb-0">
                {" "}
                {tx("Sale status")}{" "}
                <SelectInput
                  onChange={(event) => setStatus(event.target.value)}
                  value={status}
                >
                  <option value="">{tx("All")}</option>
                  {SALE_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </SelectInput>
              </label>
              {filtered ? (
                <button
                  className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline lg:mb-2"
                  onClick={() => {
                    setStatus("");
                    setMemberFilter(null);
                  }}
                  type="button"
                >
                  {" "}
                  {tx("Clear filters")}{" "}
                </button>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 p-4 sm:p-5">
            {sales.pending && sales.rows.length === 0 ? (
              <div className="grid gap-3" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading sales.")}{" "}
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
            {sales.pending && sales.rows.length > 0 ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Loading sales.")}{" "}
              </p>
            ) : null}
            {!sales.pending && sales.error ? (
              <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="text-sm font-medium text-red-800" role="alert">
                  {sales.error}
                </p>
                <button
                  className="button w-fit"
                  onClick={() => sales.reload()}
                  type="button"
                >
                  {" "}
                  {tx("Retry loading sales")}{" "}
                </button>
              </div>
            ) : null}
            {!sales.pending && !sales.error && sales.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                <p className="font-semibold text-slate-700">
                  {" "}
                  {tx("No PT sales match.")}{" "}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  {filtered
                    ? tx("Try changing or clearing the current filters.")
                    : canCreate
                      ? tx("Create a draft to add a sale.")
                      : tx("Sales will appear here when they are recorded.")}
                </p>
              </div>
            ) : null}
            {sales.rows.length > 0 ? (
              <ul aria-label={tx("Sale list")} className="grid gap-3">
                {sales.rows.map((row) => {
                  const active = selected?.id === row.id;
                  return (
                    <li key={row.id}>
                      <button
                        aria-label={saleLabel(row, tx)}
                        aria-pressed={active}
                        className={`w-full rounded-xl border border-l-4 p-4 text-left ${
                          active
                            ? "border-l-teal-700 bg-teal-50 ring-2 ring-teal-600"
                            : "border-l-slate-300 bg-white hover:bg-slate-50"
                        } border-slate-200`}
                        onClick={() => void openSale(row.id)}
                        type="button"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-xs font-semibold tracking-wide text-slate-500">
                              <time dateTime={row.service_date}>
                                {formatAppDate(row.service_date)}
                              </time>
                            </p>
                            <h3 className="text-base font-bold break-words text-slate-900">
                              {tx(row.description)}
                            </h3>
                          </div>
                          <div className="shrink-0 text-right">
                            <p className="text-lg font-black text-slate-900 tabular-nums">
                              {row.gross_fee}{" "}
                              <span className="text-xs font-semibold text-slate-500">
                                {tx(row.currency)}
                              </span>
                            </p>
                            {active ? (
                              <span className="mt-1 inline-flex rounded-full bg-teal-700 px-2.5 py-1 text-xs font-bold text-white">
                                {" "}
                                {tx("Selected")}{" "}
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <div className="mt-3">
                          <SaleStatusBadge status={row.status} />
                        </div>
                        <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3">
                          <div>
                            <dt className="text-xs text-slate-500">
                              {tx("Member")}
                            </dt>
                            <dd className="mt-0.5 font-semibold text-slate-900">
                              <IdentifierValue value={row.member_id} />
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs text-slate-500">
                              {tx("Trainer")}
                            </dt>
                            <dd className="mt-0.5 font-semibold text-slate-900">
                              <IdentifierValue
                                value={row.trainer_staff_profile_id}
                              />
                            </dd>
                          </div>
                          {row.service_package_id ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {" "}
                                {tx("Package")}{" "}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900">
                                <IdentifierValue
                                  value={row.service_package_id}
                                />
                              </dd>
                            </div>
                          ) : null}
                          {row.payment?.method ? (
                            <div>
                              <dt className="text-xs text-slate-500">
                                {" "}
                                {tx("Payment")}{" "}
                              </dt>
                              <dd className="mt-0.5 font-semibold text-slate-900">
                                {row.payment.method}
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
                bounded={sales.bounded}
                hasMore={sales.hasMore}
                index={sales.index}
                onNext={() => void sales.goNext()}
                onPrevious={() => void sales.goPrevious()}
                pending={sales.pending}
              />
            </div>
          ) : null}
        </section>

        <section
          aria-busy={detailPending || commandPending}
          aria-labelledby="sale-detail-title"
          className={cardClass}
        >
          <div className="grid gap-5 p-4 sm:p-5">
            {selected ? (
              <>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold tracking-wide text-slate-500">
                      <time dateTime={selected.service_date}>
                        {formatAppDate(selected.service_date)}
                      </time>
                    </p>
                    <h2
                      className="text-xl font-bold text-slate-900"
                      id="sale-detail-title"
                    >
                      {" "}
                      {tx("Sale detail")}{" "}
                    </h2>
                    <p className="mt-1 text-sm break-words text-slate-600">
                      {tx(selected.description)}
                    </p>
                  </div>
                  <SaleStatusBadge status={selected.status} />
                </div>
                {detailNotice ? <NoticeBanner message={detailNotice} /> : null}
                {detailPending ? (
                  <p
                    className="text-sm font-medium text-slate-600"
                    role="status"
                  >
                    {" "}
                    {tx("Loading sale detail.")}{" "}
                  </p>
                ) : null}
                {loadError ? (
                  <div className="grid gap-3">
                    <Alert message={loadError} />
                    {requestedSaleId ? (
                      <button
                        className="button w-fit"
                        onClick={() => void openSale(requestedSaleId)}
                        type="button"
                      >
                        {" "}
                        {tx("Retry loading sale")}{" "}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {actionError ? <Alert message={actionError} /> : null}
                <div>
                  <h3 className="text-sm font-bold text-slate-800">
                    {" "}
                    {tx("Financial summary")}{" "}
                  </h3>
                  <dl className="mt-3 grid grid-cols-2 gap-3">
                    <Fact
                      emphasis
                      label="Gross fee"
                      value={`${selected.gross_fee} ${tx(selected.currency)}`}
                    />
                    <Fact
                      emphasis
                      label="Outstanding"
                      value={selected.outstanding_amount}
                    />
                    <Fact
                      label="Commission"
                      value={selected.commission_amount}
                    />
                    <Fact label="Gym share" value={selected.gym_share} />
                  </dl>
                </div>
                <div className="border-t border-slate-100 pt-4">
                  <h3 className="text-sm font-bold text-slate-800">
                    {" "}
                    {tx("Package and payment")}{" "}
                  </h3>
                  <dl className="mt-3 grid grid-cols-2 gap-3">
                    <Fact label="Member" value={selected.member_id} />
                    <Fact
                      label="Trainer"
                      value={selected.trainer_staff_profile_id}
                    />
                    <Fact
                      label="Package"
                      value={selected.service_package_id ?? "None"}
                    />
                    <Fact
                      label="Payment"
                      value={selected.payment?.method ?? "None"}
                    />
                    {selected.package_price_snapshot ? (
                      <Fact
                        label="Package price"
                        value={selected.package_price_snapshot}
                      />
                    ) : null}
                    {selected.member_service_package_id ? (
                      <Fact
                        label="Granted package"
                        value={selected.member_service_package_id}
                      />
                    ) : null}
                    {selected.payment?.amount ? (
                      <Fact
                        label="Payment amount"
                        value={`${selected.payment.amount}${
                          selected.payment.currency
                            ? ` ${tx(selected.payment.currency)}`
                            : ""
                        }`}
                      />
                    ) : null}
                    {selected.payment?.business_date ? (
                      <Fact
                        label="Payment date"
                        value={formatAppDate(selected.payment.business_date)}
                      />
                    ) : null}
                    {selected.payment?.reference_number ? (
                      <Fact
                        label="Payment reference"
                        value={selected.payment.reference_number}
                      />
                    ) : null}
                    {selected.payment?.status ? (
                      <Fact
                        label="Payment status"
                        value={tx(selected.payment.status)}
                      />
                    ) : null}
                  </dl>
                  {printableReceiptIds.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {printableReceiptIds.map((id) => (
                        <ReceiptPrintLink key={id} receiptId={id} />
                      ))}
                    </div>
                  ) : null}
                </div>
                {selected.rejection_reason ? (
                  <p className="text-sm text-slate-700">
                    <span className="font-semibold">
                      {tx("Rejection reason.")}{" "}
                    </span>
                    {selected.rejection_reason}
                  </p>
                ) : null}
                {selected.reason ? (
                  <p className="text-sm text-slate-700">
                    <span className="font-semibold">{tx("Reason.")} </span>
                    {selected.reason}
                  </p>
                ) : null}
                {selected.status === "POSTED" || selected.status === "VOID" ? (
                  <div className="border-t border-slate-100 pt-4">
                    <h3 className="text-sm font-bold text-slate-800">
                      {" "}
                      {tx("Commission")}{" "}
                    </h3>
                    {commission ? (
                      <div className="mt-3 grid gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          {commission.status ? (
                            <SaleStatusBadge status={commission.status} />
                          ) : null}
                          {commission.commission_percent_snapshot ? (
                            <span className="text-sm text-slate-600">
                              {commission.commission_percent_snapshot}
                              {tx("% of")} {commission.gross_fee_snapshot}{" "}
                              {tx(commission.currency)}
                            </span>
                          ) : null}
                        </div>
                        <dl className="grid grid-cols-2 gap-3">
                          {commission.paid_amount ? (
                            <Fact
                              label="Commission paid"
                              value={commission.paid_amount}
                            />
                          ) : null}
                          {commission.outstanding_amount ? (
                            <Fact
                              label="Commission outstanding"
                              value={commission.outstanding_amount}
                            />
                          ) : null}
                        </dl>
                        {(commission.payments ?? []).length > 0 ? (
                          <ul
                            aria-label={tx("Commission payouts")}
                            className="grid gap-2"
                          >
                            {commission.payments.map((row) => (
                              <li
                                className="rounded-xl border border-slate-200 px-3 py-2"
                                key={row.id}
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <p className="font-semibold text-slate-900 tabular-nums">
                                    {row.amount}
                                  </p>
                                  <SaleStatusBadge status={row.status} />
                                </div>
                                <p className="mt-1 text-sm text-slate-600">
                                  {tx(row.payment_method)}
                                  {row.business_date
                                    ? ` · ${formatAppDate(row.business_date)}`
                                    : ""}
                                </p>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="text-sm text-slate-500">
                            {" "}
                            {tx("No payouts are recorded.")}{" "}
                          </p>
                        )}
                      </div>
                    ) : !detailPending && !commissionUnresolved ? (
                      <p className="mt-3 text-sm text-slate-500">
                        {" "}
                        {tx("No commission matched this sale.")}{" "}
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {selected.status === "DRAFT" && (canUpdate || canSubmit) ? (
                  <div
                    aria-label={tx("Sale actions")}
                    className="flex flex-wrap gap-2 border-t border-slate-100 pt-4"
                    role="group"
                  >
                    {canUpdate ? (
                      <button
                        aria-controls={
                          mode === "edit" ? "sale-draft-dialog" : undefined
                        }
                        aria-expanded={mode === "edit"}
                        className="button button-edit"
                        onClick={() => openMode("edit")}
                        ref={editButtonRef}
                        type="button"
                      >
                        {" "}
                        {tx("Edit draft")}{" "}
                      </button>
                    ) : null}
                    {canUpdate ? (
                      <button
                        aria-controls={
                          mode === "cancel" ? "cancel-sale-dialog" : undefined
                        }
                        aria-expanded={mode === "cancel"}
                        className="button button-warn"
                        onClick={() => openMode("cancel")}
                        ref={cancelButtonRef}
                        type="button"
                      >
                        {" "}
                        {tx("Cancel draft")}{" "}
                      </button>
                    ) : null}
                    {canSubmit ? (
                      <button
                        className="button"
                        disabled={detailPending || commandPending}
                        onClick={submitSale}
                        type="button"
                      >
                        {" "}
                        {tx("Submit sale")}{" "}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {selected.status === "PENDING_APPROVAL" && canApprove ? (
                  <div
                    aria-label={tx("Sale actions")}
                    className="flex flex-wrap gap-2 border-t border-slate-100 pt-4"
                    role="group"
                  >
                    {separateActor ? (
                      <>
                        <button
                          aria-controls={
                            mode === "approve"
                              ? "approve-sale-dialog"
                              : undefined
                          }
                          aria-expanded={mode === "approve"}
                          className="button button-success"
                          onClick={() => openMode("approve")}
                          ref={approveButtonRef}
                          type="button"
                        >
                          {" "}
                          {tx("Approve sale")}{" "}
                        </button>
                        <button
                          aria-controls={
                            mode === "reject" ? "reject-sale-dialog" : undefined
                          }
                          aria-expanded={mode === "reject"}
                          className="button button-warn"
                          onClick={() => openMode("reject")}
                          ref={rejectButtonRef}
                          type="button"
                        >
                          {" "}
                          {tx("Reject sale")}{" "}
                        </button>
                      </>
                    ) : (
                      <p className="text-sm text-slate-600">
                        {" "}
                        {tx(
                          "A different person must approve or reject this sale.",
                        )}{" "}
                      </p>
                    )}
                  </div>
                ) : null}
                {selected.status === "POSTED" && canVoid ? (
                  <div
                    aria-label={tx("Sale actions")}
                    className="flex flex-wrap gap-2 border-t border-slate-100 pt-4"
                    role="group"
                  >
                    <button
                      aria-controls={
                        mode === "void" ? "void-sale-dialog" : undefined
                      }
                      aria-expanded={mode === "void"}
                      className="button button-delete"
                      onClick={() => openMode("void")}
                      ref={voidButtonRef}
                      type="button"
                    >
                      {" "}
                      {tx("Void sale")}{" "}
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <h2
                  className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                  id="sale-detail-title"
                >
                  {" "}
                  {tx("Sale detail")}{" "}
                </h2>
                {loadError ? (
                  <div className="grid gap-3">
                    <Alert message={loadError} />
                    {requestedSaleId ? (
                      <button
                        className="button w-fit"
                        onClick={() => void openSale(requestedSaleId)}
                        type="button"
                      >
                        {" "}
                        {tx("Retry loading sale")}{" "}
                      </button>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">
                    {" "}
                    {tx(
                      "Select a sale to see its fee, package, payment, and commission.",
                    )}{" "}
                  </p>
                )}
              </>
            )}
          </div>
        </section>
      </div>

      {draftOpen ? (
        <SaleCommandDialog
          description={
            mode === "edit"
              ? tx(
                  "Update the draft. Leave the payment draft unchanged, replace it, or remove it.",
                )
              : tx(
                  "Choose the member, trainer, fee, and any package or payment draft.",
                )
          }
          descriptionId="sale-form-description"
          dialogId="sale-draft-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeMode}
          onSubmit={saveDraft}
          pending={commandPending}
          submitClass="button-edit"
          submitDisabled={detailPending}
          submitLabel="Save draft"
          title={mode === "edit" ? tx("Edit draft") : tx("Create draft")}
          titleId="sale-form-title"
        >
          <MemberSearch
            disabled={commandPending}
            onSelect={setMember}
            selected={member}
          />
          <Alert message={trainers.error || packages.error} />
          {trainers.pending || packages.pending ? (
            <p className="text-sm text-slate-600" role="status">
              {" "}
              {tx("Loading sale choices.")}{" "}
            </p>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Trainer">
              <SelectInput
                defaultValue={selected?.trainer_staff_profile_id ?? ""}
                name="trainer_staff_profile_id"
                required
              >
                <option value="">{tx("Select a trainer")}</option>
                {selected?.trainer_staff_profile_id &&
                !trainers.items.some(
                  (trainer) => trainer.id === selected.trainer_staff_profile_id,
                ) ? (
                  <option value={selected.trainer_staff_profile_id}>
                    {" "}
                    {tx("Current trainer")}{" "}
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
          </div>
          <Field label="Description">
            <TextInput
              defaultValue={selected?.description ?? ""}
              name="description"
              required
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Gross fee">
              <TextInput
                defaultValue={selected?.gross_fee ?? ""}
                inputMode="decimal"
                name="gross_fee"
                required
              />
            </Field>
            <Field label="PT package">
              <SelectInput
                defaultValue={selected?.service_package_id ?? ""}
                name="service_package_id"
              >
                <option value="">{tx("None")}</option>
                {packages.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.code} · {row.name} · {row.price}
                  </option>
                ))}
              </SelectInput>
            </Field>
          </div>
          <div className="grid gap-4 border-t border-slate-100 pt-4">
            <h3 className="text-sm font-bold text-slate-800">
              {tx("Payment draft")}
            </h3>
            <Field label="Payment draft">
              <SelectInput defaultValue="omit" name="payment_choice">
                <option value="omit">{tx("Leave unchanged or omit")}</option>
                <option value="set">{tx("Set payment draft")}</option>
                <option value="clear">{tx("Remove payment draft")}</option>
              </SelectInput>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Payment description">
                <TextInput name="payment_description" />
              </Field>
              <Field label="Payment amount">
                <TextInput inputMode="decimal" name="payment_amount" />
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
            </div>
            <Field label="Payment reference">
              <TextInput name="reference_number" />
            </Field>
          </div>
        </SaleCommandDialog>
      ) : null}

      {mode === "approve" && selected && canApprove ? (
        <SaleCommandDialog
          description="Approve this sale. A cash payment uses an open session for the payment business date."
          descriptionId="approve-description"
          dialogId="approve-sale-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeMode}
          onSubmit={approveSale}
          pending={commandPending}
          submitClass="button-success"
          submitDisabled={detailPending || (cashPayment && sessionsBlocked)}
          submitLabel="Confirm approval"
          title={tx("Approve sale")}
          titleId="approve-title"
        >
          {cashPayment ? (
            <>
              <p className="text-sm text-slate-600">
                {selected.payment?.business_date
                  ? tx(
                      "Open sessions are listed for the payment business date {date}.",
                      { date: formatAppDate(selected.payment.business_date) },
                    )
                  : tx(
                      "This cash payment has no business date, so a session cannot be chosen.",
                    )}
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
            <p className="text-sm text-slate-600">
              {" "}
              {tx(
                "This sale has no cash payment, so no cash session is sent.",
              )}{" "}
            </p>
          )}
        </SaleCommandDialog>
      ) : null}

      {mode === "reject" && selected && canApprove ? (
        <SaleCommandDialog
          description="Reject this sale and record the reason."
          descriptionId="reject-description"
          dialogId="reject-sale-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeMode}
          onSubmit={rejectSale}
          pending={commandPending}
          submitClass="button-warn"
          submitDisabled={detailPending}
          submitLabel="Confirm rejection"
          title={tx("Reject sale")}
          titleId="reject-title"
        >
          <ReasonField label="Rejection reason" name="rejection_reason" />
        </SaleCommandDialog>
      ) : null}

      {mode === "cancel" && selected && canUpdate ? (
        <SaleCommandDialog
          description="Cancel this draft and record the reason."
          descriptionId="cancel-sale-description"
          dialogId="cancel-sale-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeMode}
          onSubmit={cancelSale}
          pending={commandPending}
          submitClass="button-cancel"
          submitDisabled={detailPending}
          submitLabel="Confirm cancellation"
          title={tx("Cancel draft")}
          titleId="cancel-sale-title"
        >
          <ReasonField label="Cancellation reason" />
        </SaleCommandDialog>
      ) : null}

      {mode === "void" && selected && canVoid ? (
        <SaleCommandDialog
          description="Void this posted sale. Every posted payout is reversed."
          descriptionId="void-sale-description"
          dialogId="void-sale-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeMode}
          onSubmit={voidSale}
          pending={commandPending}
          submitClass="button-delete"
          submitDisabled={
            detailPending ||
            commissionUnresolved ||
            (voidNeedsSession && sessionsBlocked)
          }
          submitLabel="Confirm void"
          title={tx("Void sale")}
          titleId="void-sale-title"
        >
          {commissionUnresolved ? (
            <p className="error" role="alert">
              {" "}
              {tx(
                "The sale commission could not be loaded completely. Payout destinations are not listed, so this void was not sent.",
              )}{" "}
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
              label={tx("Payout destination for {amount}", {
                amount: row.amount,
              })}
              name={`payout_${row.id}`}
              pending={sessions.pending}
              resetKey={voidDate}
              sessions={sessions.sessions}
            />
          ))}
          <p className="text-sm text-slate-600">
            {" "}
            {tx(
              "Every posted payout is reversed. A consumed package returns PACKAGE_CONSUMED and changes nothing.",
            )}{" "}
          </p>
        </SaleCommandDialog>
      ) : null}
    </div>
  );
}

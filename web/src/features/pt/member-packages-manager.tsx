"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";

import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import { formatAppDate, formatAppDateTime } from "@/lib/date";
import {
  adjustPackageLedger,
  grantMemberPackage,
  listMemberPackages,
  listPackageLedger,
  listServicePackages,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { useLookupOptions } from "@/features/pt/lookup";
import { MemberSearch, type PickedMember } from "@/features/pt/member-search";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  MemberServicePackage,
  ServicePackage,
  ServiceSessionLedger,
} from "@/types/api";

const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

type Notice = { scope: "grant" | "correct"; message: string };

type GrantBody = {
  member_id: string;
  service_package_id: string;
  business_date: string;
  complimentary: boolean;
  reason: string;
};

function signedSessions(delta: number): string {
  if (delta > 0) return `+${delta}`;
  return String(delta);
}

function sessionTone(delta: number): string {
  if (delta > 0) return "text-emerald-700";
  if (delta < 0) return "text-red-700";
  return "text-slate-600";
}

function formatOccurred(value: string): string {
  return formatAppDateTime(value, "UTC");
}

function packageKind(value: string | undefined): "PT" | "CLASS" | "" {
  if (value === "PT" || value === "CLASS") return value;
  return "";
}

function typeBadgeClass(kind: "PT" | "CLASS"): string {
  return kind === "CLASS"
    ? "bg-sky-100 text-sky-800"
    : "bg-amber-100 text-amber-800";
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "ACTIVE":
      return "bg-emerald-100 text-emerald-800";
    case "EXHAUSTED":
      return "bg-amber-100 text-amber-800";
    case "EXPIRED":
      return "bg-slate-200 text-slate-700";
    case "VOID":
      return "bg-red-100 text-red-800";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

function grantLabel(
  row: MemberServicePackage,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  const member = row.member_name || row.member_id;
  return tx("{code} · {name} · {member}", {
    code: row.package_code,
    name: row.package_name,
    member,
  });
}

function reversibleEntries(
  rows: ServiceSessionLedger[],
): ServiceSessionLedger[] {
  return rows.filter((entry) => entry.reason !== "GRANT");
}

function grantProblem(member: PickedMember | null, body: GrantBody): string {
  if (!member) return "Select an active member.";
  if (!body.service_package_id) return "Select a package.";
  if (!body.business_date) return "Enter a business date.";
  if (!body.reason.trim()) return "Enter a reason.";
  return "";
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

function ModalForm({
  title,
  titleId,
  description,
  descriptionId,
  dialogId,
  pending,
  error,
  submitLabel,
  submitClass,
  submitDisabled,
  dialogRef,
  onSubmit,
  onClose,
  children,
}: {
  title: string;
  titleId: string;
  description: string;
  descriptionId: string;
  dialogId: string;
  pending: boolean;
  error: string;
  submitLabel: string;
  submitClass: string;
  submitDisabled?: boolean;
  dialogRef: Ref<HTMLFormElement>;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <form
        aria-busy={pending}
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-2xl"
        id={dialogId}
        noValidate
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.stopPropagation();
          event.preventDefault();
          onClose();
        }}
        onSubmit={onSubmit}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {tx(title)}
            </h2>
            <p className="mt-1 text-sm text-slate-500" id={descriptionId}>
              {tx(description)}
            </p>
          </div>
          <button
            className="button button-cancel button-sm"
            disabled={pending}
            onClick={onClose}
            type="button"
          >
            {" "}
            {tx("Close")}{" "}
          </button>
        </div>
        <fieldset
          className="m-0 grid w-full min-w-0 gap-5 border-0 px-5 py-4 sm:px-6"
          disabled={pending}
        >
          {children}
        </fieldset>
        <div className="sticky bottom-0 z-10 mt-auto border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
          {error ? (
            <div className="mb-3">
              <Alert message={error} />
            </div>
          ) : null}
          <div className="flex flex-col sm:flex-row">
            <button
              className="button button-cancel w-full sm:w-auto"
              disabled={pending}
              onClick={onClose}
              type="button"
            >
              {" "}
              {tx("Cancel")}{" "}
            </button>
            <button
              className={`button w-full sm:w-auto ${submitClass}`}
              disabled={pending || submitDisabled}
              type="submit"
            >
              {tx(submitLabel)}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function GrantTypeBadge({ kind }: { kind: "PT" | "CLASS" }) {
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-bold ${typeBadgeClass(kind)}`}
    >
      {kind}
    </span>
  );
}

function GrantStatusBadge({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusBadgeClass(status)}`}
    >
      {tx(status)}
    </span>
  );
}

function LedgerReferences({ entry }: { entry: ServiceSessionLedger }) {
  const tx = useTx();
  if (!entry.pt_booking_id && !entry.class_booking_id) return null;
  return (
    <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
      {entry.pt_booking_id ? (
        <span>
          {" "}
          {tx("PT booking")} <IdentifierValue value={entry.pt_booking_id} />
        </span>
      ) : null}
      {entry.class_booking_id ? (
        <span>
          {" "}
          {tx("class booking")}{" "}
          <IdentifierValue value={entry.class_booking_id} />
        </span>
      ) : null}
    </p>
  );
}

function LedgerEntry({ entry }: { entry: ServiceSessionLedger }) {
  const tx = useTx();
  return (
    <li className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-bold tracking-wide text-slate-900">
            {entry.reason}
            <span className="sr-only"> · {entry.session_delta}</span>
          </p>
          <p className="mt-1 text-sm text-slate-600">
            <time dateTime={entry.occurred_at}>
              {formatOccurred(entry.occurred_at)}
            </time>
          </p>
        </div>
        <p
          className={`min-w-12 text-right text-lg font-black tabular-nums ${sessionTone(entry.session_delta)}`}
        >
          {signedSessions(entry.session_delta)}
          <span className="sr-only"> {tx("sessions")}</span>
        </p>
      </div>
      <LedgerReferences entry={entry} />
    </li>
  );
}

export function MemberPackagesManager({
  canAdjust,
  today,
}: {
  canAdjust: boolean;
  today: string;
}) {
  const tx = useTx();
  const [status, setStatus] = useState("");
  const [packageType, setPackageType] = useState("");
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [selected, setSelected] = useState<MemberServicePackage | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [grantError, setGrantError] = useState("");
  const [correctError, setCorrectError] = useState("");
  const [reversesId, setReversesId] = useState("");
  const [pending, setPending] = useState(false);
  const [grantFreePack, setGrantFreePack] = useState(true);
  const [grantNote, setGrantNote] = useState("Free pack grant");
  const [mode, setMode] = useState<"grant" | "correct" | null>(null);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const pendingRef = useRef(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  const grantButtonRef = useRef<HTMLButtonElement>(null);
  const correctButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<"grant" | "correct" | null>(null);
  const grants = useCursorPages<MemberServicePackage>(
    `grants:${status}:${packageType}:${memberFilter?.id ?? ""}`,
    (after) =>
      listMemberPackages({
        limit: PAGE,
        ...(after ? { after } : {}),
        ...(status ? { status } : {}),
        ...(packageType ? { package_type: packageType } : {}),
        ...(memberFilter ? { member_id: memberFilter.id } : {}),
      }),
  );
  const ledgerRequest = useRef(0);
  const [ledgerStamp, setLedgerStamp] = useState<string | null>(null);
  const [ledgerRows, setLedgerRows] = useState<ServiceSessionLedger[]>([]);
  const ledger = useCursorPages<ServiceSessionLedger>(
    selected ? `ledger:${selected.id}` : "ledger:none",
    (after) => {
      const request = ++ledgerRequest.current;
      const stamp = selected?.id ?? null;
      const result = selected
        ? listPackageLedger(selected.id, {
            limit: PAGE,
            ...(after ? { after } : {}),
          })
        : Promise.resolve({
            data: [] as ServiceSessionLedger[],
            meta: { has_more: false, next_after: null },
          });
      return result.then(
        (page) => {
          if (request === ledgerRequest.current) {
            setLedgerRows(page.data);
            setLedgerStamp(stamp);
          }
          return page;
        },
        (caught: unknown) => {
          if (request === ledgerRequest.current) {
            if (!after) setLedgerRows([]);
            setLedgerStamp(stamp);
          }
          throw caught;
        },
      );
    },
  );
  const catalog = useLookupOptions<ServicePackage>(
    "grant-catalog",
    (after) =>
      listServicePackages({
        limit: "100",
        is_active: "true",
        ...(after ? { after } : {}),
      }),
    mode === "grant",
  );
  const ledgerReady = selected !== null && ledgerStamp === selected.id;
  const filtered = status !== "" || packageType !== "" || memberFilter !== null;
  const showGrantPaging = grants.index > 0 || grants.hasMore || grants.bounded;
  const showLedgerPaging =
    ledgerReady && (ledger.index > 0 || ledger.hasMore || ledger.bounded);
  const visibleLedger = ledgerReady ? ledgerRows : [];
  const reversible = reversibleEntries(visibleLedger);
  const reversal = reversible.find((entry) => entry.id === reversesId) ?? null;

  useEffect(() => {
    if (mode === "grant" || mode === "correct") {
      restoreFocus.current = mode;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    const previous = restoreFocus.current;
    if (!previous) return;
    restoreFocus.current = null;
    const target =
      previous === "grant" ? grantButtonRef.current : correctButtonRef.current;
    target?.focus();
  }, [mode]);

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  function chooseGrant(row: MemberServicePackage) {
    setSelected(row);
    setMode((current) => (current === "correct" ? null : current));
  }

  function clearSelectionForFilters() {
    setSelected(null);
    setMode((current) => (current === "correct" ? null : current));
  }

  function closeModal() {
    if (pendingRef.current) return;
    setGrantError("");
    setCorrectError("");
    setMode(null);
  }

  function openGrant() {
    if (pendingRef.current || mode !== null) return;
    setGrantError("");
    setGrantFreePack(true);
    setGrantNote("Free pack grant");
    setNotice((current) => (current?.scope === "grant" ? null : current));
    setMode("grant");
  }

  function chooseFreePack(next: boolean) {
    setGrantFreePack(next);
    setGrantNote(next ? "Free pack grant" : "Paid pack grant");
  }

  function openCorrect() {
    if (pendingRef.current || mode !== null || !selected) return;
    setCorrectError("");
    setReversesId("");
    setNotice((current) => (current?.scope === "correct" ? null : current));
    setMode("correct");
  }

  function submitGrant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    const form = new FormData(event.currentTarget);
    const body: GrantBody = {
      member_id: member?.id ?? "",
      service_package_id: String(form.get("service_package_id") ?? ""),
      business_date: String(form.get("business_date") ?? ""),
      complimentary: form.get("complimentary") === "true",
      reason: String(form.get("reason") ?? ""),
    };
    const problem = grantProblem(member, body);
    if (problem) {
      setGrantError(problem);
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setGrantError("");
    void grantMemberPackage(body, commandKey(JSON.stringify(body)))
      .then(() => {
        key.current = null;
        setNotice({ scope: "grant", message: tx("Package granted.") });
        setGrantError("");
        setMode(null);
        grants.reload();
        ledger.reload();
      })
      .catch((caught: unknown) => {
        setGrantError(phase5Error(caught, tx("The grant failed.")));
      })
      .finally(() => {
        pendingRef.current = false;
        setPending(false);
      });
  }

  function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current || !selected) return;
    const form = new FormData(event.currentTarget);
    const body = {
      reverses_entry_id: String(form.get("reverses_entry_id") ?? ""),
      reason: String(form.get("reason") ?? ""),
    };
    if (!body.reverses_entry_id) {
      setCorrectError("Choose a ledger entry to reverse.");
      return;
    }
    if (!reversible.some((entry) => entry.id === body.reverses_entry_id)) {
      setCorrectError("Choose a ledger entry that is not a grant.");
      return;
    }
    if (!body.reason.trim()) {
      setCorrectError("Enter a correction reason.");
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setCorrectError("");
    void adjustPackageLedger(
      selected.id,
      body,
      commandKey(JSON.stringify(body)),
    )
      .then(() => {
        key.current = null;
        setNotice({ scope: "correct", message: tx("Ledger corrected.") });
        setCorrectError("");
        setMode(null);
        grants.reload();
        ledger.reload();
      })
      .catch((caught: unknown) => {
        setCorrectError(phase5Error(caught, tx("The correction failed.")));
      })
      .finally(() => {
        pendingRef.current = false;
        setPending(false);
      });
  }

  return (
    <div className="grid gap-6">
      <div inert={mode !== null}>
        <div className="grid gap-6">
          <section
            aria-busy={grants.pending}
            aria-labelledby="grant-catalog-title"
            className={cardClass}
          >
            <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
              <div>
                <h2
                  className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                  id="grant-catalog-title"
                >
                  {" "}
                  {tx("Granted packages")}{" "}
                </h2>
                <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                  {" "}
                  {tx(
                    "Member, package, remaining sessions, and validity.",
                  )}{" "}
                </p>
              </div>
              {canAdjust ? (
                <button
                  aria-controls={
                    mode === "grant" ? "grant-package-dialog" : undefined
                  }
                  aria-expanded={mode === "grant"}
                  className="button button-create w-full sm:w-auto"
                  onClick={openGrant}
                  ref={grantButtonRef}
                  type="button"
                >
                  <span aria-hidden="true">+</span> {tx("Grant package")}{" "}
                </button>
              ) : null}
            </div>

            <div className="border-b border-slate-200 bg-slate-50/70 px-4 py-3 sm:px-5">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                <div className="grid gap-3 sm:grid-cols-2 lg:w-80 lg:shrink-0">
                  <Field label="Grant status">
                    <SelectInput
                      onChange={(event) => {
                        setStatus(event.target.value);
                        clearSelectionForFilters();
                      }}
                      value={status}
                    >
                      <option value="">{tx("All")}</option>
                      <option value="ACTIVE">{tx("ACTIVE")}</option>
                      <option value="EXHAUSTED">{tx("EXHAUSTED")}</option>
                      <option value="EXPIRED">{tx("EXPIRED")}</option>
                      <option value="VOID">{tx("VOID")}</option>
                    </SelectInput>
                  </Field>
                  <Field label="Package type">
                    <SelectInput
                      onChange={(event) => {
                        setPackageType(event.target.value);
                        clearSelectionForFilters();
                      }}
                      value={packageType}
                    >
                      <option value="">{tx("All")}</option>
                      <option value="PT">{tx("PT")}</option>
                      <option value="CLASS">{tx("CLASS")}</option>
                    </SelectInput>
                  </Field>
                </div>
                <div className="min-w-0 flex-1">
                  <MemberSearch
                    label="Filter grants by member"
                    onSelect={(picked) => {
                      setMemberFilter(picked);
                      clearSelectionForFilters();
                    }}
                    selected={memberFilter}
                  />
                </div>
                {filtered ? (
                  <button
                    className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline lg:mb-2"
                    onClick={() => {
                      setStatus("");
                      setPackageType("");
                      setMemberFilter(null);
                      clearSelectionForFilters();
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
              {notice?.scope === "grant" ? (
                <NoticeBanner message={notice.message} />
              ) : null}
              {grants.pending && grants.rows.length === 0 ? (
                <div className="grid gap-3" role="status">
                  <p className="text-sm font-medium text-slate-600">
                    {" "}
                    {tx("Loading grants.")}{" "}
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
              {grants.pending && grants.rows.length > 0 ? (
                <p className="text-sm font-medium text-slate-600" role="status">
                  {" "}
                  {tx("Loading grants.")}{" "}
                </p>
              ) : null}
              {!grants.pending && grants.error ? (
                <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-medium text-red-800" role="alert">
                    {grants.error}
                  </p>
                  <button
                    className="button w-fit"
                    onClick={() => grants.reload()}
                    type="button"
                  >
                    {" "}
                    {tx("Retry loading grants")}{" "}
                  </button>
                </div>
              ) : null}
              {!grants.pending && !grants.error && grants.rows.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                  <p className="font-semibold text-slate-700">
                    {" "}
                    {tx("No grants match.")}{" "}
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    {filtered
                      ? tx("Try changing or clearing the current filters.")
                      : canAdjust
                        ? tx("Grant a package to add it to this list.")
                        : tx(
                            "Grants will appear here when a package is granted.",
                          )}
                  </p>
                </div>
              ) : null}
              {grants.rows.length > 0 ? (
                <div
                  className={`w-full overflow-x-auto ${grants.pending ? "opacity-60" : ""}`}
                >
                  <table className="w-full min-w-[36rem] text-left text-sm">
                    <caption className="sr-only">
                      {tx("Granted packages")}
                    </caption>
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr>
                        <th
                          className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                          scope="col"
                        >
                          {tx("Member")}
                        </th>
                        <th
                          className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                          scope="col"
                        >
                          {tx("Package")}
                        </th>
                        <th
                          className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                          scope="col"
                        >
                          {tx("Type")}
                        </th>
                        <th
                          className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                          scope="col"
                        >
                          {tx("Left")}
                        </th>
                        <th
                          className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                          scope="col"
                        >
                          {tx("Status")}
                        </th>
                        <th
                          className="hidden px-4 py-2.5 text-xs font-semibold sm:px-5 md:table-cell"
                          scope="col"
                        >
                          {tx("Validity")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {grants.rows.map((row) => {
                        const active = selected?.id === row.id;
                        const kind = packageKind(row.package_type);
                        return (
                          <tr
                            className={
                              active
                                ? "bg-teal-50 shadow-[inset_3px_0_0_#0f766e]"
                                : "hover:bg-slate-50"
                            }
                            key={row.id}
                          >
                            <td className="max-w-[14rem] px-4 py-3 sm:px-5">
                              <button
                                aria-current={active ? "true" : undefined}
                                aria-label={grantLabel(row, tx)}
                                className="text-left font-semibold break-words text-slate-900"
                                onClick={() => chooseGrant(row)}
                                type="button"
                              >
                                {row.member_name || (
                                  <IdentifierValue value={row.member_id} />
                                )}
                              </button>
                            </td>
                            <td className="max-w-[14rem] px-4 py-3 sm:px-5">
                              <p className="font-semibold break-words text-slate-900">
                                {row.package_name}
                              </p>
                              <p className="mt-0.5 text-xs text-slate-500">
                                {row.package_code}
                              </p>
                            </td>
                            <td className="px-4 py-3 sm:px-5">
                              {kind ? <GrantTypeBadge kind={kind} /> : null}
                            </td>
                            <td className="px-4 py-3 font-semibold whitespace-nowrap text-slate-800 tabular-nums sm:px-5">
                              {row.remaining_sessions}/{row.granted_sessions}
                            </td>
                            <td className="px-4 py-3 sm:px-5">
                              <GrantStatusBadge status={row.status} />
                            </td>
                            <td className="hidden px-4 py-3 whitespace-nowrap text-slate-600 md:table-cell sm:px-5">
                              {formatAppDate(row.valid_from)} –{" "}
                              {formatAppDate(row.valid_until)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
            {showGrantPaging ? (
              <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
                <CursorControls
                  bounded={grants.bounded}
                  hasMore={grants.hasMore}
                  index={grants.index}
                  onNext={() => void grants.goNext()}
                  onPrevious={() => void grants.goPrevious()}
                  pending={grants.pending}
                />
              </div>
            ) : null}
          </section>

          <section
            aria-busy={Boolean(selected) && ledger.pending}
            aria-labelledby="ledger-title"
            className={`${cardClass} ${selected ? "ring-2 ring-teal-600" : ""}`}
          >
            <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
              <div>
                <h2
                  className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                  id="ledger-title"
                >
                  {" "}
                  {tx("Session ledger")}{" "}
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  {" "}
                  {tx("Reason, session change, and booking references.")}{" "}
                </p>
              </div>
              {canAdjust && selected && ledgerReady ? (
                <button
                  aria-controls={
                    mode === "correct" ? "correct-ledger-dialog" : undefined
                  }
                  aria-expanded={mode === "correct"}
                  className="button button-edit w-full sm:w-auto"
                  onClick={openCorrect}
                  ref={correctButtonRef}
                  type="button"
                >
                  {" "}
                  {tx("Correct ledger")}{" "}
                </button>
              ) : null}
            </div>

            {selected ? (
              <div className="border-b border-teal-100 bg-teal-50 px-4 py-3 sm:px-5">
                <p className="text-xs font-semibold tracking-wide text-teal-800 uppercase">
                  {" "}
                  {tx("Viewing")}{" "}
                </p>
                <p className="mt-1 font-bold text-slate-900">
                  {selected.package_code}
                  <span className="ml-2 font-semibold text-slate-600">
                    {selected.package_name}
                  </span>
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {" "}
                  {tx("Member")}{" "}
                  {selected.member_name || (
                    <IdentifierValue value={selected.member_id} />
                  )}
                </p>
              </div>
            ) : null}

            <div className="grid gap-4 p-4 sm:p-5">
              {notice?.scope === "correct" ? (
                <NoticeBanner message={notice.message} />
              ) : null}
              {!selected ? (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
                  <p className="font-semibold text-slate-700">
                    {" "}
                    {tx("Choose a grant to read its ledger.")}{" "}
                  </p>
                </div>
              ) : null}
              {selected && !ledgerReady ? (
                <div className="grid gap-3" role="status">
                  <p className="text-sm font-medium text-slate-600">
                    {" "}
                    {tx("Loading the ledger.")}{" "}
                  </p>
                  <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
                </div>
              ) : null}
              {selected && ledgerReady && ledger.pending ? (
                <p className="text-sm font-medium text-slate-600" role="status">
                  {" "}
                  {tx("Loading the ledger.")}{" "}
                </p>
              ) : null}
              {selected && ledgerReady && !ledger.pending && ledger.error ? (
                <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-medium text-red-800" role="alert">
                    {ledger.error}
                  </p>
                  <button
                    className="button w-fit"
                    onClick={() => ledger.reload()}
                    type="button"
                  >
                    {" "}
                    {tx("Retry loading the ledger")}{" "}
                  </button>
                </div>
              ) : null}
              {selected &&
              ledgerReady &&
              !ledger.pending &&
              !ledger.error &&
              visibleLedger.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
                  <p className="font-semibold text-slate-700">
                    {" "}
                    {tx("This grant has no ledger rows.")}{" "}
                  </p>
                </div>
              ) : null}
              {selected && ledgerReady && visibleLedger.length > 0 ? (
                <ul
                  aria-label={tx("Session ledger entries")}
                  className="grid gap-2"
                >
                  {visibleLedger.map((entry) => (
                    <LedgerEntry entry={entry} key={entry.id} />
                  ))}
                </ul>
              ) : null}
            </div>
            {showLedgerPaging ? (
              <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
                <CursorControls
                  bounded={ledger.bounded}
                  hasMore={ledger.hasMore}
                  index={ledger.index}
                  onNext={() => void ledger.goNext()}
                  onPrevious={() => void ledger.goPrevious()}
                  pending={ledger.pending}
                />
              </div>
            ) : null}
          </section>
        </div>
      </div>

      {mode === "grant" && canAdjust ? (
        <ModalForm
          description="Give this member their class or PT ticket pack so they can book."
          descriptionId="grant-package-description"
          dialogId="grant-package-dialog"
          dialogRef={dialogRef}
          error={grantError}
          onClose={closeModal}
          onSubmit={submitGrant}
          pending={pending}
          submitClass="button-create"
          submitLabel="Save grant"
          title={tx("Give pack to member")}
          titleId="grant-title"
        >
          <MemberSearch
            disabled={pending}
            onSelect={setMember}
            selected={member}
          />
          {catalog.pending ? (
            <p className="text-sm text-slate-500" role="status">
              {" "}
              {tx("Loading packages.")}{" "}
            </p>
          ) : null}
          <Alert message={catalog.error} />
          <Field label="Which pack?">
            <SelectInput name="service_package_id" required>
              <option value="">{tx("Select a package")}</option>
              {catalog.items.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.type} · {row.code} · {row.name}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Business date">
            <TextInput
              defaultValue={today}
              name="business_date"
              required
              type="date"
            />
          </Field>
          <Field label="Free pack?">
            <SelectInput
              name="complimentary"
              onChange={(event) =>
                chooseFreePack(event.target.value === "true")
              }
              value={grantFreePack ? "true" : "false"}
            >
              <option value="true">{tx("Yes")}</option>
              <option value="false">{tx("No")}</option>
            </SelectInput>
          </Field>
          <p className="text-sm text-slate-500">
            {" "}
            {grantFreePack
              ? tx("Yes = give the pack for free.")
              : tx(
                  "No = member already paid. Record the payment in Payments first. Paid PT packs still come from a PT sale.",
                )}{" "}
          </p>
          <Field label="Short note" required>
            <textarea
              className="input min-h-24"
              name="reason"
              onChange={(event) => setGrantNote(event.target.value)}
              required
              value={grantNote}
            />
          </Field>
          <p className="-mt-3 text-xs text-slate-500">
            {" "}
            {tx("Filled from Free pack. Edit only if you need a clearer note.")}{" "}
          </p>
        </ModalForm>
      ) : null}

      {mode === "correct" && canAdjust && selected ? (
        <ModalForm
          description="Reverse one ledger entry on this package. Grant entries cannot be corrected."
          descriptionId="correct-ledger-description"
          dialogId="correct-ledger-dialog"
          dialogRef={dialogRef}
          error={correctError}
          onClose={closeModal}
          onSubmit={submitCorrection}
          pending={pending}
          submitClass="button-edit"
          submitDisabled={reversible.length === 0}
          submitLabel="Save correction"
          title={tx("Correct ledger")}
          titleId="correct-title"
        >
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              {" "}
              {tx("Selected package")}{" "}
            </p>
            <p className="mt-1 text-base font-bold text-slate-900">
              {selected.package_code}
              <span className="ml-2 font-semibold text-slate-600">
                {selected.package_name}
              </span>
            </p>
            <p className="mt-2 text-sm text-slate-600">
              {" "}
              {tx("Member")}{" "}
              {selected.member_name || (
                <IdentifierValue value={selected.member_id} />
              )}
            </p>
            <p className="mt-1 text-sm text-slate-700">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusBadgeClass(selected.status)}`}
              >
                {tx(selected.status)}
              </span>
              <span className="sr-only"> · </span>
              <span className="ml-2 font-semibold">
                {tx("remaining")} {selected.remaining_sessions} {tx("of")}{" "}
                {selected.granted_sessions}
              </span>
            </p>
          </div>
          <Field label="Ledger entry to reverse">
            <SelectInput
              name="reverses_entry_id"
              onChange={(event) => setReversesId(event.target.value)}
              required
              value={reversesId}
            >
              <option value="">{tx("Select an entry")}</option>
              {reversible.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.reason} · {signedSessions(entry.session_delta)} ·{" "}
                  {formatOccurred(entry.occurred_at)}
                </option>
              ))}
            </SelectInput>
          </Field>
          {reversal ? (
            <div className="rounded-xl border border-slate-200 p-4">
              <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {" "}
                {tx("Reversal entry")}{" "}
              </p>
              <div className="mt-2 flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-bold text-slate-900">
                    {reversal.reason}
                    <span className="sr-only"> · {reversal.session_delta}</span>
                  </p>
                  <p className="mt-1 text-sm text-slate-600">
                    <time dateTime={reversal.occurred_at}>
                      {formatOccurred(reversal.occurred_at)}
                    </time>
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {" "}
                    {tx("Entry")} <IdentifierValue value={reversal.id} />
                  </p>
                </div>
                <p
                  className={`text-lg font-black tabular-nums ${sessionTone(reversal.session_delta)}`}
                >
                  {signedSessions(reversal.session_delta)}
                </p>
              </div>
              <LedgerReferences entry={reversal} />
            </div>
          ) : null}
          <p className="text-sm text-slate-500">
            {reversible.length === 0
              ? tx(
                  "No reversible entries are on this page. Grant entries cannot be corrected. Move to another page to reach an earlier entry.",
                )
              : tx(
                  "Reversal choices are the ledger entries on this page, except grants. Move to another page to reach an earlier entry.",
                )}
          </p>
          <ReasonField label="Correction reason" />
        </ModalForm>
      ) : null}
    </div>
  );
}

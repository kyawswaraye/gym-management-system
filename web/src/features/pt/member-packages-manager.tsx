"use client";

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
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes} UTC`;
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

function typeBorderClass(kind: "PT" | "CLASS" | ""): string {
  if (kind === "CLASS") return "border-l-sky-500";
  if (kind === "PT") return "border-l-amber-400";
  return "border-l-slate-300";
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

function grantLabel(row: MemberServicePackage): string {
  return `${row.package_code} · ${row.package_name} · member ${row.member_id}`;
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
  return (
    <p
      aria-live="polite"
      className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
      role="status"
    >
      {message}
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
              {title}
            </h2>
            <p className="mt-1 text-sm text-slate-500" id={descriptionId}>
              {description}
            </p>
          </div>
          <button
            className="button button-cancel button-sm"
            disabled={pending}
            onClick={onClose}
            type="button"
          >
            Close
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
              Cancel
            </button>
            <button
              className={`button w-full sm:w-auto ${submitClass}`}
              disabled={pending || submitDisabled}
              type="submit"
            >
              {submitLabel}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function GrantRow({
  row,
  selected,
  onSelect,
}: {
  row: MemberServicePackage;
  selected: boolean;
  onSelect: (row: MemberServicePackage) => void;
}) {
  const kind = packageKind(row.package_type);
  return (
    <li>
      <button
        aria-label={grantLabel(row)}
        aria-pressed={selected}
        className={`w-full rounded-xl border border-l-4 border-slate-200 p-4 text-left ${typeBorderClass(kind)} ${
          selected
            ? "bg-teal-50 ring-2 ring-teal-600"
            : "bg-white hover:bg-slate-50"
        }`}
        onClick={() => onSelect(row)}
        type="button"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-wide text-slate-500">
              {row.package_code}
            </p>
            <h3 className="text-base font-bold break-words text-slate-900">
              {row.package_name}
            </h3>
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
            {kind ? (
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-bold ${typeBadgeClass(kind)}`}
              >
                {kind}
              </span>
            ) : null}
            {selected ? (
              <span className="rounded-full bg-teal-700 px-2.5 py-1 text-xs font-bold text-white">
                Selected
              </span>
            ) : null}
          </div>
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusBadgeClass(row.status)}`}
          >
            {row.status}
          </span>
          <span className="sr-only"> · </span>
          <span className="text-sm font-semibold text-slate-700">
            remaining {row.remaining_sessions} of {row.granted_sessions}
          </span>
        </p>
        <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 sm:grid-cols-3">
          <div className="col-span-2 sm:col-span-1">
            <dt className="text-xs text-slate-500">Validity</dt>
            <dd className="mt-0.5 font-semibold text-slate-900">
              {row.valid_from} to {row.valid_until}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Member</dt>
            <dd className="mt-0.5 font-semibold text-slate-900">
              <IdentifierValue value={row.member_id} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Package</dt>
            <dd className="mt-0.5 font-semibold text-slate-900">
              <IdentifierValue value={row.service_package_id} />
            </dd>
          </div>
        </dl>
      </button>
    </li>
  );
}

function LedgerReferences({ entry }: { entry: ServiceSessionLedger }) {
  if (!entry.pt_booking_id && !entry.class_booking_id) return null;
  return (
    <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
      {entry.pt_booking_id ? (
        <span>
          PT booking <IdentifierValue value={entry.pt_booking_id} />
        </span>
      ) : null}
      {entry.class_booking_id ? (
        <span>
          class booking <IdentifierValue value={entry.class_booking_id} />
        </span>
      ) : null}
    </p>
  );
}

function LedgerEntry({ entry }: { entry: ServiceSessionLedger }) {
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
          <span className="sr-only"> sessions</span>
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
    setNotice((current) => (current?.scope === "grant" ? null : current));
    setMode("grant");
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
        setNotice({ scope: "grant", message: "Package granted." });
        setGrantError("");
        setMode(null);
        grants.reload();
        ledger.reload();
      })
      .catch((caught: unknown) => {
        setGrantError(phase5Error(caught, "The grant failed."));
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
        setNotice({ scope: "correct", message: "Ledger corrected." });
        setCorrectError("");
        setMode(null);
        grants.reload();
        ledger.reload();
      })
      .catch((caught: unknown) => {
        setCorrectError(phase5Error(caught, "The correction failed."));
      })
      .finally(() => {
        pendingRef.current = false;
        setPending(false);
      });
  }

  return (
    <div className="grid gap-6">
      <div inert={mode !== null}>
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]">
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
                  Granted packages
                </h2>
                <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                  Code, type, balance, and validity for each grant.
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
                  <span aria-hidden="true">+</span>
                  Grant package
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
                      <option value="">All</option>
                      <option value="ACTIVE">ACTIVE</option>
                      <option value="EXHAUSTED">EXHAUSTED</option>
                      <option value="EXPIRED">EXPIRED</option>
                      <option value="VOID">VOID</option>
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
                      <option value="">All</option>
                      <option value="PT">PT</option>
                      <option value="CLASS">CLASS</option>
                    </SelectInput>
                  </Field>
                </div>
                <div className="min-w-0 flex-1">
                  <MemberSearch
                    action="Search grant members"
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
                    Clear filters
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
                    Loading grants.
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
                  Loading grants.
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
                    Retry loading grants
                  </button>
                </div>
              ) : null}
              {!grants.pending && !grants.error && grants.rows.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                  <p className="font-semibold text-slate-700">
                    No grants match.
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    {filtered
                      ? "Try changing or clearing the current filters."
                      : canAdjust
                        ? "Grant a package to add it to this list."
                        : "Grants will appear here when a package is granted."}
                  </p>
                </div>
              ) : null}
              {grants.rows.length > 0 ? (
                <ul aria-label="Granted packages" className="grid gap-3">
                  {grants.rows.map((row) => (
                    <GrantRow
                      key={row.id}
                      onSelect={chooseGrant}
                      row={row}
                      selected={selected?.id === row.id}
                    />
                  ))}
                </ul>
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
                  Session ledger
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  Reason, session change, and booking references.
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
                  Correct ledger
                </button>
              ) : null}
            </div>

            {selected ? (
              <div className="border-b border-teal-100 bg-teal-50 px-4 py-3 sm:px-5">
                <p className="text-xs font-semibold tracking-wide text-teal-800 uppercase">
                  Viewing
                </p>
                <p className="mt-1 font-bold text-slate-900">
                  {selected.package_code}
                  <span className="ml-2 font-semibold text-slate-600">
                    {selected.package_name}
                  </span>
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  Member <IdentifierValue value={selected.member_id} />
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
                    Choose a grant to read its ledger.
                  </p>
                </div>
              ) : null}
              {selected && !ledgerReady ? (
                <div className="grid gap-3" role="status">
                  <p className="text-sm font-medium text-slate-600">
                    Loading the ledger.
                  </p>
                  <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
                </div>
              ) : null}
              {selected && ledgerReady && ledger.pending ? (
                <p className="text-sm font-medium text-slate-600" role="status">
                  Loading the ledger.
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
                    Retry loading the ledger
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
                    This grant has no ledger rows.
                  </p>
                </div>
              ) : null}
              {selected && ledgerReady && visibleLedger.length > 0 ? (
                <ul aria-label="Session ledger entries" className="grid gap-2">
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
          description="Choose an active member and package. A paid PT grant comes from sale approval."
          descriptionId="grant-package-description"
          dialogId="grant-package-dialog"
          dialogRef={dialogRef}
          error={grantError}
          onClose={closeModal}
          onSubmit={submitGrant}
          pending={pending}
          submitClass="button-create"
          submitLabel="Save grant"
          title="Grant package"
          titleId="grant-title"
        >
          <MemberSearch
            disabled={pending}
            onSelect={setMember}
            selected={member}
          />
          {catalog.pending ? (
            <p className="text-sm text-slate-500" role="status">
              Loading packages.
            </p>
          ) : null}
          <Alert message={catalog.error} />
          <Field label="Package">
            <SelectInput name="service_package_id" required>
              <option value="">Select a package</option>
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
          <Field label="Complimentary">
            <SelectInput defaultValue="true" name="complimentary">
              <option value="true">Yes</option>
              <option value="false">No</option>
            </SelectInput>
          </Field>
          <p className="text-sm text-slate-500">
            A PT package must be complimentary. A paid PT grant comes from sale
            approval.
          </p>
          <ReasonField label="Reason" />
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
          title="Correct ledger"
          titleId="correct-title"
        >
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              Selected package
            </p>
            <p className="mt-1 text-base font-bold text-slate-900">
              {selected.package_code}
              <span className="ml-2 font-semibold text-slate-600">
                {selected.package_name}
              </span>
            </p>
            <p className="mt-2 text-sm text-slate-600">
              Member <IdentifierValue value={selected.member_id} />
            </p>
            <p className="mt-1 text-sm text-slate-700">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusBadgeClass(selected.status)}`}
              >
                {selected.status}
              </span>
              <span className="sr-only"> · </span>
              <span className="ml-2 font-semibold">
                remaining {selected.remaining_sessions} of{" "}
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
              <option value="">Select an entry</option>
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
                Reversal entry
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
                    Entry <IdentifierValue value={reversal.id} />
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
              ? "No reversible entries are on this page. Grant entries cannot be corrected. Move to another page to reach an earlier entry."
              : "Reversal choices are the ledger entries on this page, except grants. Move to another page to reach an earlier entry."}
          </p>
          <ReasonField label="Correction reason" />
        </ModalForm>
      ) : null}
    </div>
  );
}

"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveSalaryPeriod,
  cancelSalaryPeriod,
  createSalaryPeriod,
  getSalaryPeriod,
  listSalaryPeriods,
  rejectSalaryPeriod,
  replaceSalaryLines,
  submitSalaryPeriod,
  updateSalaryPeriod,
  voidSalaryPeriod,
} from "@/features/finance/client";
import { DialogFrame, FormDialog } from "@/components/form-dialog";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { actionButtonClass } from "@/lib/ui/action-button";
import { listStaff } from "@/features/operations/client";
import type {
  PaymentMethod,
  SalaryLineWriteRequest,
  SalaryPeriod,
  StaffRecord,
  StaffSalaryLine,
  VoidSalaryRequest,
} from "@/types/api";

const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;
const STATUSES = [
  ["", "All"],
  ["DRAFT", "Draft"],
  ["PENDING_APPROVAL", "Pending"],
  ["POSTED", "Posted"],
  ["REJECTED", "Rejected"],
  ["CANCELLED", "Cancelled"],
  ["VOID", "Void"],
] as const;
const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const fieldTone =
  "[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600";

type SalaryAction =
  "edit" | "submit" | "approve" | "reject" | "cancel" | "void" | null;
type ErrorScope = "create" | "edit" | "lines" | "action" | "detail";

type LineDraft = {
  key: string;
  staffId: string;
  staffLabel: string;
  amount: string;
  method: PaymentMethod;
  reference: string;
  description: string;
};

function pretty(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function statusTone(status: string): string {
  const tones: Record<string, string> = {
    DRAFT: "bg-slate-100 text-slate-700",
    PENDING_APPROVAL: "bg-amber-100 text-amber-800",
    POSTED: "bg-emerald-100 text-emerald-800",
    REJECTED: "bg-red-100 text-red-700",
    CANCELLED: "bg-slate-200 text-slate-600",
    VOID: "bg-rose-100 text-rose-700",
  };
  return tones[status] ?? "bg-slate-100 text-slate-600";
}

function nextStep(
  status: string,
  abilities: {
    canUpdate: boolean;
    canSubmit: boolean;
    canApprove: boolean;
  },
): string {
  switch (status) {
    case "DRAFT":
      if (abilities.canUpdate) {
        return "Add each staff amount, then submit this draft.";
      }
      if (abilities.canSubmit) {
        return "Submit this draft when the staff lines are ready.";
      }
      return "This draft is waiting for the next step.";
    case "PENDING_APPROVAL":
      return abilities.canApprove
        ? "Approve to post every line, or reject it with a reason."
        : "Waiting for approval.";
    case "POSTED":
      return "Correct a posted period only by voiding it in full.";
    case "REJECTED":
      return "Rejected periods stay closed.";
    case "CANCELLED":
      return "Cancelled drafts stay closed.";
    case "VOID":
      return "Voided periods stay closed.";
    default:
      return "";
  }
}

function personLabel(person: {
  staff_code: string;
  display_name: string;
}): string {
  return `${person.staff_code} — ${person.display_name}`;
}

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function blankLine(): LineDraft {
  return {
    key: crypto.randomUUID(),
    staffId: "",
    staffLabel: "",
    amount: "",
    method: "BANK_TRANSFER",
    reference: "",
    description: "",
  };
}

function StatusPill({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold tracking-wide ${statusTone(status)}`}
    >
      {tx(pretty(status))}
    </span>
  );
}

function LoadingRows({ label }: { label: string }) {
  const tx = useTx();
  return (
    <div className="grid gap-3 p-4 sm:p-5" role="status">
      <p className="text-sm font-medium text-slate-600">{tx(label)}</p>
      <div className="grid gap-2">
        {[0, 1, 2].map((item) => (
          <div
            className="h-14 animate-pulse rounded-xl bg-slate-100"
            key={item}
          />
        ))}
      </div>
    </div>
  );
}

function ActionPanel({
  title,
  hint,
  pending,
  error,
  confirmLabel,
  onSubmit,
  onCancel,
  children,
}: {
  title: string;
  hint?: string;
  pending: boolean;
  error: string;
  confirmLabel: string;
  onSubmit: (form: FormData) => void;
  onCancel: () => void;
  children?: ReactNode;
}) {
  const tx = useTx();
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(new FormData(event.currentTarget));
  }

  return (
    <FormDialog
      description={hint}
      onClose={onCancel}
      onSubmit={submit}
      pending={pending}
      title={title}
    >
      <Alert message={error} />
      <div className={fieldTone}>{children}</div>
      <div>
        <button
          className="button button-cancel"
          disabled={pending}
          onClick={onCancel}
          type="button"
        >
          {" "}
          {tx("Back")}{" "}
        </button>
        <button
          className={actionButtonClass(confirmLabel)}
          disabled={pending}
          type="submit"
        >
          {tx(confirmLabel)}
        </button>
      </div>
    </FormDialog>
  );
}

function StatusFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const tx = useTx();
  const groupRef = useRef<HTMLDivElement>(null);

  function move(event: KeyboardEvent<HTMLDivElement>, delta: number) {
    event.preventDefault();
    const index = STATUSES.findIndex(([item]) => item === value);
    const next =
      STATUSES[(index + delta + STATUSES.length) % STATUSES.length]?.[0] ?? "";
    onChange(next);
    window.requestAnimationFrame(() => {
      groupRef.current
        ?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
        ?.focus();
    });
  }

  return (
    <div
      aria-label={tx("Status")}
      className="flex flex-wrap gap-1.5"
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          move(event, 1);
        }
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          move(event, -1);
        }
      }}
      ref={groupRef}
      role="radiogroup"
    >
      {STATUSES.map(([item, label]) => {
        const selected = value === item;
        return (
          <button
            aria-checked={selected}
            className={
              selected
                ? "rounded-full bg-slate-900 px-3 py-1.5 text-xs font-bold text-white shadow-sm"
                : "rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-200"
            }
            key={label}
            onClick={() => onChange(item)}
            role="radio"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            {tx(label)}
          </button>
        );
      })}
    </div>
  );
}

function StaffLineList({ lines }: { lines: StaffSalaryLine[] }) {
  const tx = useTx();
  if (lines.length === 0) {
    return (
      <p className="rounded-xl bg-slate-50 px-4 py-8 text-center text-sm text-slate-500 ring-1 ring-slate-200/80">
        {" "}
        {tx("No staff lines on this period.")}{" "}
      </p>
    );
  }

  return (
    <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200/80">
      {lines.map((line) => (
        <li
          className="flex items-start justify-between gap-3 bg-white px-4 py-3"
          key={line.id}
        >
          <div className="min-w-0">
            <p className="font-semibold text-slate-900">
              {line.staff.display_name}
            </p>
            <p className="mt-0.5 text-xs leading-5 text-slate-500">
              {line.staff.staff_code} · {tx(pretty(line.payment_method))}
              {line.description ? ` · ${line.description}` : ""}
            </p>
            {line.external_reference ? (
              <p className="mt-0.5 text-xs text-slate-400">
                {line.external_reference}
              </p>
            ) : null}
          </div>
          <p className="shrink-0 text-sm font-bold text-slate-900 tabular-nums">
            {line.amount}{" "}
            <span className="font-medium text-slate-400">{tx(line.currency)}</span>
          </p>
        </li>
      ))}
    </ul>
  );
}

function LineEditor({
  lines,
  staff,
  currency,
  pending,
  error,
  amountError,
  invalidAmountKey,
  onPatch,
  onRemove,
  onAdd,
  onSave,
}: {
  lines: LineDraft[];
  staff: StaffRecord[];
  currency: string;
  pending: boolean;
  error: string;
  amountError: string | undefined;
  invalidAmountKey: string;
  onPatch: (key: string, patch: Partial<LineDraft>) => void;
  onRemove: (key: string) => void;
  onAdd: () => void;
  onSave: () => void;
}) {
  const tx = useTx();
  return (
    <div className={`grid gap-3 ${fieldTone}`}>
      <div>
        <h3 className="text-sm font-bold text-slate-900">
          {tx("Staff lines")}
        </h3>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          {" "}
          {tx(
            "Each person appears once. Saving replaces every line on this draft.",
          )}{" "}
        </p>
      </div>
      {staff.length === 0 ? (
        <p className="text-sm text-slate-500">
          {" "}
          {tx("No staff records were returned.")}{" "}
        </p>
      ) : null}
      <Alert message={error} />
      {lines.map((line, index) => {
        const known = staff.some((person) => person.id === line.staffId);
        return (
          <div
            className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            key={line.key}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                  {" "}
                  {tx("Line")} {index + 1}
                </p>
                <p className="mt-0.5 truncate text-sm font-bold text-slate-900">
                  {line.staffLabel || tx("Choose a staff member")}
                </p>
              </div>
              <button
                aria-label={tx("Remove line {number}", { number: index + 1 })}
                className="text-sm font-bold text-red-700 hover:text-red-900 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={lines.length < 2}
                onClick={() => onRemove(line.key)}
                type="button"
              >
                {" "}
                {tx("Remove")}{" "}
              </button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Staff">
                <SelectInput
                  onChange={(event) => {
                    const staffId = event.target.value;
                    const person = staff.find((item) => item.id === staffId);
                    onPatch(line.key, {
                      staffId,
                      staffLabel: person ? personLabel(person) : "",
                    });
                  }}
                  value={line.staffId}
                >
                  <option value="">{tx("Select staff")}</option>
                  {line.staffId && !known ? (
                    <option value={line.staffId}>
                      {line.staffLabel || "Selected staff"}
                    </option>
                  ) : null}
                  {staff.map((person) => (
                    <option key={person.id} value={person.id}>
                      {personLabel(person)}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <Field
                error={line.key === invalidAmountKey ? amountError : undefined}
                label={tx("Amount ({currency})", { currency: tx(currency) })}
              >
                <TextInput
                  inputMode="decimal"
                  onChange={(event) =>
                    onPatch(line.key, { amount: event.target.value })
                  }
                  value={line.amount}
                />
              </Field>
              <Field label="Method">
                <SelectInput
                  onChange={(event) =>
                    onPatch(line.key, {
                      method: event.target.value as PaymentMethod,
                    })
                  }
                  value={line.method}
                >
                  {METHODS.map((item) => (
                    <option key={item} value={item}>
                      {tx(pretty(item))}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <Field label="Reference">
                <TextInput
                  onChange={(event) =>
                    onPatch(line.key, { reference: event.target.value })
                  }
                  value={line.reference}
                />
              </Field>
              <div className="sm:col-span-2">
                <Field label="Description">
                  <TextInput
                    onChange={(event) =>
                      onPatch(line.key, { description: event.target.value })
                    }
                    value={line.description}
                  />
                </Field>
              </div>
            </div>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          className="button button-cancel"
          disabled={lines.length >= 200}
          onClick={onAdd}
          type="button"
        >
          {" "}
          {tx("Add staff line")}{" "}
        </button>
        <button
          className="button button-edit"
          disabled={pending}
          onClick={onSave}
          type="button"
        >
          {" "}
          {tx("Replace staff lines")}{" "}
        </button>
      </div>
    </div>
  );
}

export function SalariesManager({
  canCreate,
  canUpdate,
  canSubmit,
  canApprove,
  canVoid,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  currency: string;
  defaultDate: string;
}) {
  const tx = useTx();
  const [applied, setApplied] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [period, setPeriod] = useState<SalaryPeriod | null>(null);
  const [staff, setStaff] = useState<StaffRecord[]>([]);
  const [lines, setLines] = useState<LineDraft[]>([blankLine()]);
  const [action, setAction] = useState<SalaryAction>(null);
  const [recording, setRecording] = useState(false);
  const [linesOpen, setLinesOpen] = useState(false);
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [errorScope, setErrorScope] = useState<ErrorScope | null>(null);
  const [invalidAmountKey, setInvalidAmountKey] = useState("");
  const [pending, setPending] = useState(false);
  const periods = useCursorPages(applied, (after) =>
    listSalaryPeriods({
      ...(applied ? { status: applied } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const detail = period?.id === selectedId ? period : null;
  const approvalDate =
    action === "approve" && detail
      ? detail.business_date
      : action === "void"
        ? correctionDate
        : "";
  const sessions = useOpenCashSessions(approvalDate);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void listStaff({ limit: 100 })
        .then((page) => {
          if (active) setStaff(page.data);
        })
        .catch(() => {
          if (active) setStaff([]);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getSalaryPeriod(selectedId)
        .then((row) => {
          if (!active) return;
          setPeriod(row);
          setLines(
            row.lines.length
              ? row.lines.map((line) => ({
                  key: line.id,
                  staffId: line.staff_profile_id,
                  staffLabel: personLabel(line.staff),
                  amount: line.amount,
                  method: line.payment_method as PaymentMethod,
                  reference: line.external_reference,
                  description: line.description,
                }))
              : [blankLine()],
          );
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setPeriod(null);
          setFormError(
            financeError(caught, tx("The salary period could not be loaded.")),
          );
          setErrorScope("detail");
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, periods.rows, tx]);

  const detailId = detail?.id ?? "";
  useEffect(() => {
    if (!detailId) return;
    document.getElementById("salary-detail-title")?.focus();
    document
      .getElementById("salary-detail")
      ?.scrollIntoView?.({ block: "nearest" });
  }, [detailId]);

  function clearFeedback() {
    setFormError("");
    setFields({});
    setErrorScope(null);
    setInvalidAmountKey("");
  }

  function fail(caught: unknown, fallback: string, scope: ErrorScope) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
    setErrorScope(scope);
  }

  function selectPeriod(id: string) {
    setSelectedId(id);
    setAction(null);
    clearFeedback();
  }

  function openAction(next: Exclude<SalaryAction, null>) {
    setAction(next);
    clearFeedback();
    if (next === "void") setCorrectionDate(defaultDate);
  }

  function closePeriod() {
    setSelectedId("");
    setPeriod(null);
    setAction(null);
    setLines([blankLine()]);
    clearFeedback();
  }

  function patchLine(key: string, patch: Partial<LineDraft>) {
    if (invalidAmountKey === key && patch.amount !== undefined) {
      setInvalidAmountKey("");
      setFields((current) => {
        const next = { ...current };
        delete next.amount;
        return next;
      });
    }
    setLines((current) =>
      current.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    );
  }

  function linePayload(): SalaryLineWriteRequest[] | null {
    if (lines.length < 1 || lines.length > 200) {
      setFields({});
      setInvalidAmountKey("");
      setFormError("Enter from 1 to 200 staff lines.");
      setErrorScope("lines");
      return null;
    }
    const staffIds = lines.map((line) => line.staffId);
    if (
      new Set(staffIds).size !== staffIds.length ||
      staffIds.some((id) => !id)
    ) {
      setFields({});
      setInvalidAmountKey("");
      setFormError("Choose each staff member once.");
      setErrorScope("lines");
      return null;
    }
    const payload: SalaryLineWriteRequest[] = [];
    for (const line of lines) {
      const amount = canonicalMoney(line.amount);
      if (!amount) {
        setInvalidAmountKey(line.key);
        setFields({ amount: "Enter an exact amount such as 100.00." });
        setFormError("");
        setErrorScope("lines");
        return null;
      }
      if (!line.description.trim()) {
        setInvalidAmountKey("");
        setFields({});
        setFormError("Enter a description for each staff line.");
        setErrorScope("lines");
        return null;
      }
      payload.push({
        staff_profile_id: line.staffId,
        amount,
        currency,
        payment_method: line.method,
        description: line.description.trim(),
        ...(line.reference ? { external_reference: line.reference } : {}),
      });
    }
    return payload;
  }

  const cashLines =
    detail?.lines.filter((line) => line.payment_method === "CASH") ?? [];
  const showPaging = periods.index > 0 || periods.hasMore || periods.bounded;
  const createError = errorScope === "create" ? formError : "";
  const editError = errorScope === "edit" ? formError : "";
  const lineError = errorScope === "lines" ? formError : "";
  const actionError = errorScope === "action" ? formError : "";
  const detailError = errorScope === "detail" ? formError : "";
  const canEditDraft = detail?.status === "DRAFT" && canUpdate;
  const canSubmitDraft = detail?.status === "DRAFT" && canSubmit;
  const canDecide = detail?.status === "PENDING_APPROVAL" && canApprove;
  const canCancelDraft = detail?.status === "DRAFT" && canUpdate;
  const canVoidPosted = detail?.status === "POSTED" && canVoid;
  const hasRowActions = Boolean(
    canEditDraft ||
    canSubmitDraft ||
    canDecide ||
    canCancelDraft ||
    canVoidPosted,
  );
  const editingLines = Boolean(
    canEditDraft && (action === null || action === "edit"),
  );
  const guidance = detail
    ? nextStep(detail.status, { canUpdate, canSubmit, canApprove })
    : "";

  return (
    <div className="grid gap-6">
      {canCreate && recording ? (
        <FormDialog
          description="Save the dates first. Add each staff amount after the draft opens."
          onClose={() => {
            if (pending) return;
            setRecording(false);
            clearFeedback();
          }}
          onSubmit={(event) => {
            event.preventDefault();
            const formElement = event.currentTarget;
            const form = new FormData(formElement);
            setPending(true);
            void createSalaryPeriod({
              period_start: text(form, "period_start"),
              period_end: text(form, "period_end"),
              business_date: text(form, "business_date"),
              description: text(form, "description"),
            })
              .then((created) => {
                formElement.reset();
                setRecording(false);
                setSelectedId(created.id);
                setPeriod(created);
                setAction(null);
                clearFeedback();
                periods.reload();
              })
              .catch((caught: unknown) =>
                fail(caught, "The period could not be created.", "create"),
              )
              .finally(() => setPending(false));
          }}
          pending={pending}
          title={tx("New salary period")}
          wide
        >
          <div className={fieldTone}>
            <Alert message={createError} />
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Period start">
                <TextInput
                  defaultValue={defaultDate}
                  name="period_start"
                  required
                  type="date"
                />
              </Field>
              <Field label="Period end">
                <TextInput
                  defaultValue={defaultDate}
                  name="period_end"
                  required
                  type="date"
                />
              </Field>
              <Field label="Business date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  required
                  type="date"
                />
              </Field>
              <Field label="Description">
                <TextInput name="description" required />
              </Field>
            </div>
          </div>
          <div>
            <button
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              {" "}
              {tx("Save period")}{" "}
            </button>
          </div>
        </FormDialog>
      ) : null}

      <div
        className={
          selectedId
            ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.95fr)]"
            : "grid"
        }
      >
        <section
          aria-busy={periods.pending}
          aria-labelledby="salary-list-title"
          className={cardClass}
        >
          <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2
                className="text-lg font-bold text-slate-900"
                id="salary-list-title"
              >
                {" "}
                {tx("Salary periods")}{" "}
              </h2>
              {canCreate ? (
                <button
                  className="button button-create"
                  onClick={() => {
                    setRecording(true);
                    setAction(null);
                    clearFeedback();
                  }}
                  type="button"
                >
                  {" "}
                  {tx("New salary period")}{" "}
                </button>
              ) : null}
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
              {" "}
              {tx(
                "Periods shown are only those the server returns for your access. Reception staff see their own drafts and submissions. Amounts are the server's line amounts.",
              )}{" "}
            </p>
            <div className="mt-4">
              <StatusFilter onChange={setApplied} value={applied} />
            </div>
          </div>

          {periods.pending && periods.rows.length === 0 ? (
            <LoadingRows label="Loading salary periods." />
          ) : null}
          {periods.pending && periods.rows.length > 0 ? (
            <p
              className="px-5 pt-4 text-sm font-medium text-slate-600"
              role="status"
            >
              {" "}
              {tx("Loading salary periods.")}{" "}
            </p>
          ) : null}
          {!periods.pending && periods.error ? (
            <div className="p-4 sm:p-5">
              <Alert message={periods.error} />
            </div>
          ) : null}
          {!periods.pending && !periods.error && periods.rows.length === 0 ? (
            <div className="px-4 py-14 text-center sm:px-5">
              <p className="font-semibold text-slate-800">
                {applied
                  ? tx("No salary periods match this status.")
                  : tx("No salary periods yet.")}
              </p>
              <p className="mt-1 text-sm text-slate-500">
                {applied
                  ? tx("Try another status.")
                  : tx("Drafts you save will show up here.")}
              </p>
            </div>
          ) : null}
          {periods.rows.length > 0 ? (
            <div
              className={`overflow-x-auto ${periods.pending ? "opacity-60" : ""}`}
            >
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{tx("Salary periods")}</caption>
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Period")}{" "}
                    </th>
                    <th
                      className="hidden px-5 py-2.5 text-xs font-semibold md:table-cell"
                      scope="col"
                    >
                      {" "}
                      {tx("Business date")}{" "}
                    </th>
                    <th
                      className="hidden px-5 py-2.5 text-xs font-semibold sm:table-cell"
                      scope="col"
                    >
                      {" "}
                      {tx("Staff")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Status")}{" "}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {periods.rows.map((row) => {
                    const selected = selectedId === row.id;
                    return (
                      <tr
                        className={
                          selected
                            ? "bg-[#e7f3ff] shadow-[inset_3px_0_0_#0866ff]"
                            : "hover:bg-slate-50"
                        }
                        key={row.id}
                      >
                        <td className="px-5 py-3">
                          <button
                            aria-current={selected ? "true" : undefined}
                            className="text-left font-semibold whitespace-nowrap"
                            onClick={() => selectPeriod(row.id)}
                            type="button"
                          >
                            {row.period_start} – {row.period_end}
                          </button>
                          {row.description ? (
                            <p className="mt-1 line-clamp-1 max-w-md text-xs text-slate-500">
                              {tx(row.description)}
                            </p>
                          ) : null}
                        </td>
                        <td className="hidden px-5 py-3 whitespace-nowrap text-slate-600 tabular-nums md:table-cell">
                          {row.business_date}
                        </td>
                        <td className="hidden px-5 py-3 text-slate-600 tabular-nums sm:table-cell">
                          {row.lines.length}
                        </td>
                        <td className="px-5 py-3">
                          <StatusPill status={row.status} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {showPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={periods.bounded}
                hasMore={periods.hasMore}
                index={periods.index}
                onNext={() => void periods.goNext()}
                onPrevious={() => void periods.goPrevious()}
                pending={periods.pending}
              />
            </div>
          ) : null}
        </section>

        {selectedId ? (
          <section
            aria-labelledby="salary-detail-title"
            className="rounded-2xl border border-slate-200 border-t-[3px] border-t-amber-500 bg-white shadow-sm xl:sticky xl:top-4 xl:max-h-[calc(100dvh-2rem)] xl:overflow-y-auto"
            id="salary-detail"
          >
            {!detail && detailError ? (
              <div className="grid gap-3 p-5">
                <div className="flex justify-end">
                  <button
                    className="button button-cancel button-sm"
                    onClick={closePeriod}
                    type="button"
                  >
                    {" "}
                    {tx("Close")}{" "}
                  </button>
                </div>
                <h2 className="sr-only" id="salary-detail-title">
                  {" "}
                  {tx("Salary period")}{" "}
                </h2>
                <Alert message={detailError} />
              </div>
            ) : null}
            {!detail && !detailError ? (
              <div className="grid gap-3 p-5" role="status">
                <h2 className="sr-only" id="salary-detail-title">
                  {" "}
                  {tx("Salary period")}{" "}
                </h2>
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading the salary period.")}{" "}
                </p>
                <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
                <div className="grid grid-cols-2 gap-2">
                  <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                  <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                </div>
              </div>
            ) : null}
            {detail ? (
              <>
                <div className="px-5 pt-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2
                        className="text-xs font-semibold tracking-[0.16em] text-slate-400 uppercase focus:outline-none"
                        id="salary-detail-title"
                        tabIndex={-1}
                      >
                        {" "}
                        {tx("Salary period")}{" "}
                      </h2>
                      <p className="mt-1 text-xl font-black tracking-tight text-slate-900 tabular-nums">
                        {detail.period_start} – {detail.period_end}
                      </p>
                      <p className="mt-1 text-sm leading-6 break-words text-slate-500">
                        {tx(detail.description)}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <StatusPill status={detail.status} />
                      <button
                        className="button button-cancel button-sm"
                        onClick={closePeriod}
                        type="button"
                      >
                        {" "}
                        {tx("Close")}{" "}
                      </button>
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                      <p className="text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Business date")}{" "}
                      </p>
                      <p className="mt-1 text-sm font-bold text-slate-900 tabular-nums">
                        {detail.business_date}
                      </p>
                    </div>
                    <div className="rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                      <p className="text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Staff")}{" "}
                      </p>
                      <p className="mt-1 text-sm font-bold text-slate-900 tabular-nums">
                        {detail.lines.length}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="grid gap-4 px-5 py-4">
                  {guidance && detail.status === "POSTED" ? (
                    <p
                      className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950"
                      role="note"
                    >
                      {guidance}
                    </p>
                  ) : null}
                  {guidance && detail.status !== "POSTED" ? (
                    <p className="text-sm leading-6 text-slate-500">
                      {guidance}
                    </p>
                  ) : null}
                  {detail.rejection_reason ? (
                    <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-900">
                      <span className="mb-1 block text-xs font-semibold text-red-700">
                        {" "}
                        {tx("Rejection reason")}{" "}
                      </span>
                      {detail.rejection_reason}
                    </p>
                  ) : null}
                  {detail.reason &&
                  (detail.status === "VOID" ||
                    detail.status === "CANCELLED") ? (
                    <p className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                      <span className="mb-1 block text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Reason")}{" "}
                      </span>
                      {detail.reason}
                      {detail.void_business_date ? (
                        <span className="mt-2 block text-xs text-slate-500">
                          {" "}
                          {tx("Correction date")} {detail.void_business_date}
                        </span>
                      ) : null}
                    </p>
                  ) : null}

                  {action === "edit" &&
                  canUpdate &&
                  detail.status === "DRAFT" ? (
                    <FormDialog
                      description="Dates and the description stay on this draft."
                      key={detail.id}
                      onClose={() => {
                        if (pending) return;
                        setAction(null);
                        clearFeedback();
                      }}
                      onSubmit={(event) => {
                        event.preventDefault();
                        const form = new FormData(event.currentTarget);
                        setPending(true);
                        void updateSalaryPeriod(detail.id, {
                          period_start: text(form, "period_start"),
                          period_end: text(form, "period_end"),
                          business_date: text(form, "business_date"),
                          description: text(form, "description"),
                        })
                          .then((updated) => {
                            setPeriod(updated);
                            setAction(null);
                            clearFeedback();
                            periods.reload();
                          })
                          .catch((caught: unknown) =>
                            fail(
                              caught,
                              "The period could not be updated.",
                              "edit",
                            ),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Edit period")}
                    >
                      <div className={fieldTone}>
                        <Alert message={editError} />
                        <Field label="Period start">
                          <TextInput
                            defaultValue={detail.period_start}
                            name="period_start"
                            required
                            type="date"
                          />
                        </Field>
                        <Field label="Period end">
                          <TextInput
                            defaultValue={detail.period_end}
                            name="period_end"
                            required
                            type="date"
                          />
                        </Field>
                        <Field label="Business date">
                          <TextInput
                            defaultValue={detail.business_date}
                            name="business_date"
                            required
                            type="date"
                          />
                        </Field>
                        <Field label="Description">
                          <TextInput
                            defaultValue={detail.description}
                            name="description"
                            required
                          />
                        </Field>
                        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
                          <button
                            className="button button-cancel"
                            disabled={pending}
                            onClick={() => setAction(null)}
                            type="button"
                          >
                            {" "}
                            {tx("Back")}{" "}
                          </button>
                          <button
                            className="button button-edit"
                            disabled={pending}
                            type="submit"
                          >
                            {" "}
                            {tx("Save period changes")}{" "}
                          </button>
                        </div>
                      </div>
                    </FormDialog>
                  ) : null}

                  {editingLines ? (
                    <button
                      className="button button-edit"
                      onClick={() => setLinesOpen(true)}
                      type="button"
                    >
                      {" "}
                      {tx("Edit staff lines")}{" "}
                    </button>
                  ) : null}
                  {editingLines && linesOpen ? (
                    <DialogFrame
                      description="Each person appears once. Saving replaces every line on this draft."
                      onClose={() => {
                        if (!pending) setLinesOpen(false);
                      }}
                      pending={pending}
                      title={tx("Staff lines")}
                      wide
                    >
                      <LineEditor
                        amountError={fields.amount}
                        currency={currency}
                        error={lineError}
                        invalidAmountKey={invalidAmountKey}
                        lines={lines}
                        onAdd={() =>
                          setLines((current) => [...current, blankLine()])
                        }
                        onPatch={patchLine}
                        onRemove={(key) =>
                          setLines((current) =>
                            current.filter((item) => item.key !== key),
                          )
                        }
                        onSave={() => {
                          const payload = linePayload();
                          if (!payload || !detail) return;
                          clearFeedback();
                          setPending(true);
                          void replaceSalaryLines(detail.id, { lines: payload })
                            .then((updated) => {
                              setPeriod(updated);
                              setLinesOpen(false);
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The lines could not be replaced.",
                                "lines",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        staff={staff}
                      />
                    </DialogFrame>
                  ) : null}
                  {detail.lines ? (
                    <div className="grid gap-2">
                      <h3 className="text-sm font-bold text-slate-900">
                        {" "}
                        {tx("Staff lines")}{" "}
                      </h3>
                      <StaffLineList lines={detail.lines} />
                    </div>
                  ) : null}
                </div>

                {hasRowActions || action ? (
                  <div className="grid gap-3 border-t border-slate-100 px-5 py-4">
                    {action === null && hasRowActions ? (
                      <div className="flex flex-wrap gap-2">
                        {canEditDraft ? (
                          <button
                            className="button button-edit"
                            onClick={() => openAction("edit")}
                            type="button"
                          >
                            {" "}
                            {tx("Edit period")}{" "}
                          </button>
                        ) : null}
                        {canSubmitDraft ? (
                          <button
                            className="button"
                            onClick={() => openAction("submit")}
                            type="button"
                          >
                            {" "}
                            {tx("Submit")}{" "}
                          </button>
                        ) : null}
                        {canDecide ? (
                          <button
                            className="button button-success"
                            onClick={() => openAction("approve")}
                            type="button"
                          >
                            {" "}
                            {tx("Approve")}{" "}
                          </button>
                        ) : null}
                        {canDecide ? (
                          <button
                            className="button button-warn"
                            onClick={() => openAction("reject")}
                            type="button"
                          >
                            {" "}
                            {tx("Reject")}{" "}
                          </button>
                        ) : null}
                        {canCancelDraft ? (
                          <button
                            className="button button-cancel"
                            onClick={() => openAction("cancel")}
                            type="button"
                          >
                            {" "}
                            {tx("Cancel draft")}{" "}
                          </button>
                        ) : null}
                        {canVoidPosted ? (
                          <button
                            className="button button-delete"
                            onClick={() => openAction("void")}
                            type="button"
                          >
                            {" "}
                            {tx("Void period")}{" "}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {action === "submit" ? (
                      <ActionPanel
                        confirmLabel="Confirm submit"
                        error={actionError}
                        hint="This sends the draft for approval."
                        onCancel={() => setAction(null)}
                        onSubmit={() => {
                          setPending(true);
                          void submitSalaryPeriod(detail.id)
                            .then((row) => {
                              setPeriod(row);
                              setAction(null);
                              clearFeedback();
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The period could not be submitted.",
                                "action",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Submit this salary period?")}
                      />
                    ) : null}
                    {action === "approve" ? (
                      <ActionPanel
                        confirmLabel="Confirm approval"
                        error={actionError}
                        hint="Approval posts every staff line."
                        onCancel={() => setAction(null)}
                        onSubmit={(form) => {
                          const mappings = cashLines.map((line) => ({
                            line_id: line.id,
                            cash_session_id: text(form, `session-${line.id}`),
                          }));
                          if (mappings.some((row) => !row.cash_session_id)) {
                            setFields({
                              cash_session_id:
                                "Map every cash line to an open session.",
                            });
                            setFormError(
                              "Map every cash line to an open session.",
                            );
                            setErrorScope("action");
                            return;
                          }
                          setPending(true);
                          void approveSalaryPeriod(
                            detail.id,
                            mappings.length ? { cash_sessions: mappings } : {},
                          )
                            .then(() => getSalaryPeriod(detail.id))
                            .then((row) => {
                              setPeriod(row);
                              setAction(null);
                              clearFeedback();
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The period could not be approved.",
                                "action",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Approve salary period")}
                      >
                        {cashLines.length === 0 ? (
                          <p className="text-sm text-slate-600">
                            {" "}
                            {tx("No cash line is mapped.")}{" "}
                          </p>
                        ) : null}
                        {cashLines.map((line) => (
                          <CashSessionSelect
                            collectionError={sessions.error}
                            currency={line.currency}
                            error={fields.cash_session_id}
                            key={line.id}
                            label={tx("Session for {name}", {
                              name: line.staff.display_name,
                            })}
                            name={`session-${line.id}`}
                            pending={sessions.pending}
                            sessions={sessions.sessions}
                          />
                        ))}
                        {detail.lines
                          .filter((line) => line.payment_method !== "CASH")
                          .map((line) => (
                            <p
                              className="rounded-lg bg-white px-3 py-2 text-sm leading-6 text-slate-600 ring-1 ring-slate-200"
                              key={line.id}
                            >
                              {tx(
                                "{name} pays by {method} and is not mapped to a session.",
                                {
                                  name: line.staff.display_name,
                                  method: tx(line.payment_method),
                                },
                              )}{" "}
                            </p>
                          ))}
                      </ActionPanel>
                    ) : null}
                    {action === "reject" ? (
                      <ActionPanel
                        confirmLabel="Confirm rejection"
                        error={actionError}
                        hint="The period stays unposted and keeps your reason."
                        onCancel={() => setAction(null)}
                        onSubmit={(form) => {
                          setPending(true);
                          void rejectSalaryPeriod(detail.id, {
                            rejection_reason: text(form, "rejection_reason"),
                          })
                            .then((row) => {
                              setPeriod(row);
                              setAction(null);
                              clearFeedback();
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The period could not be rejected.",
                                "action",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Reject salary period")}
                      >
                        <ReasonField
                          label="Rejection reason"
                          name="rejection_reason"
                        />
                      </ActionPanel>
                    ) : null}
                    {action === "cancel" ? (
                      <ActionPanel
                        confirmLabel="Confirm cancellation"
                        error={actionError}
                        hint="A cancelled draft cannot be submitted."
                        onCancel={() => setAction(null)}
                        onSubmit={(form) => {
                          setPending(true);
                          void cancelSalaryPeriod(detail.id, {
                            reason: text(form, "reason"),
                          })
                            .then((row) => {
                              setPeriod(row);
                              setAction(null);
                              clearFeedback();
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The draft could not be cancelled.",
                                "action",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Cancel salary draft")}
                      >
                        <ReasonField label="Cancellation reason" />
                      </ActionPanel>
                    ) : null}
                    {action === "void" ? (
                      <ActionPanel
                        confirmLabel="Confirm void"
                        error={actionError}
                        hint="Voiding reverses the whole period. The original lines stay on record."
                        onCancel={() => setAction(null)}
                        onSubmit={(form) => {
                          const mappings = cashLines.map((line) => ({
                            line_id: line.id,
                            cash_session_id: text(form, `void-${line.id}`),
                          }));
                          if (mappings.some((row) => !row.cash_session_id)) {
                            setFields({
                              cash_session_id:
                                "Map every cash line to a destination session.",
                            });
                            setFormError(
                              "Map every cash line to a destination session.",
                            );
                            setErrorScope("action");
                            return;
                          }
                          const body: VoidSalaryRequest = {
                            reason: text(form, "reason"),
                          };
                          const date = text(form, "business_date");
                          if (date) body.business_date = date;
                          if (mappings.length) body.cash_sessions = mappings;
                          setPending(true);
                          void voidSalaryPeriod(detail.id, body)
                            .then((result) => {
                              setPeriod(result.salary_period);
                              setAction(null);
                              clearFeedback();
                              periods.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(
                                caught,
                                "The period could not be voided.",
                                "action",
                              ),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Void salary period")}
                      >
                        <ReasonField label="Void reason" />
                        <Field label="Correction date">
                          <TextInput
                            defaultValue={defaultDate}
                            name="business_date"
                            onChange={(event) =>
                              setCorrectionDate(event.target.value)
                            }
                            type="date"
                          />
                        </Field>
                        {cashLines.map((line) => (
                          <CashSessionSelect
                            collectionError={sessions.error}
                            currency={line.currency}
                            error={fields.cash_session_id}
                            key={line.id}
                            label={tx("Destination session for {name}", {
                              name: line.staff.display_name,
                            })}
                            name={`void-${line.id}`}
                            pending={sessions.pending}
                            sessions={sessions.sessions}
                          />
                        ))}
                      </ActionPanel>
                    ) : null}
                  </div>
                ) : null}
              </>
            ) : null}
          </section>
        ) : null}
      </div>
    </div>
  );
}

"use client";
import { useTx } from "@/i18n/use-tx";

import type {
  FormEvent,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

import { FormDialog } from "@/components/form-dialog";
import { actionButtonClass } from "@/lib/ui/action-button";
import { DigitInput } from "@/lib/ui/digit-input";
import { englishDigitsModeFromInput } from "@/lib/ui/english-digits";
import { IdentifierValue } from "@/lib/ui/identifier";

export function Alert({ message }: { message: string }) {
  const tx = useTx();
  if (!message) return null;
  return (
    <p className="error" role="alert">
      {tx(message)}
    </p>
  );
}

export function Loading({ label }: { label: string }) {
  const tx = useTx();
  return (
    <p className="muted" role="status">
      {tx(label)}
    </p>
  );
}

export function Empty({ label }: { label: string }) {
  const tx = useTx();
  return <p className="muted">{tx(label)}</p>;
}

export function StatusText({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold">
      {tx(status)}
    </span>
  );
}

export function CursorControls({
  pending,
  index,
  hasMore,
  bounded,
  onPrevious,
  onNext,
}: {
  pending: boolean;
  index: number;
  hasMore: boolean;
  bounded: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const tx = useTx();
  const showPrevious = index > 0;
  const showNext = hasMore;
  if (!showPrevious && !showNext && !bounded) return null;

  return (
    <nav
      aria-label={tx("Pagination")}
      className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4"
    >
      <div className="flex items-center gap-2">
        {showPrevious && (
          <button
            className="button button-cancel"
            disabled={pending}
            onClick={onPrevious}
            type="button"
          >
            <span aria-hidden="true">←</span> {tx("Previous page")}{" "}
          </button>
        )}
        {showNext && (
          <button
            className="button"
            disabled={pending}
            onClick={onNext}
            type="button"
          >
            {" "}
            {tx("Next page")} <span aria-hidden="true">→</span>
          </button>
        )}
        {(showPrevious || showNext) && (
          <span className="ml-1 text-xs font-medium text-slate-500">
            {tx("Page {page}", { page: index + 1 })}
          </span>
        )}
      </div>
      {bounded && (
        <p className="text-sm text-amber-700">
          {" "}
          {tx("Refine the filters to see older rows.")}{" "}
        </p>
      )}
    </nav>
  );
}

export function Field({
  label,
  error,
  required = false,
  children,
}: {
  label: string;
  error?: string | undefined;
  required?: boolean;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <label className="field">
      <span>
        {tx(label)}
        {required ? (
          <abbr
            aria-hidden="true"
            className="required-mark"
            title={tx("Required")}
          />
        ) : null}
      </span>
      {children}
      {error && <span className="text-sm text-red-700">{tx(error)}</span>}
    </label>
  );
}

export function TextInput({
  error,
  decimalPlaces,
  allowNegative,
  keepLeadingZeros,
  type,
  inputMode,
  step,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  error?: string;
  decimalPlaces?: number;
  allowNegative?: boolean;
  keepLeadingZeros?: boolean;
}) {
  const mode = englishDigitsModeFromInput({
    type,
    inputMode,
    step,
    decimalPlaces,
    allowNegative,
    keepLeadingZeros,
  });
  const ariaInvalid = error ? true : props["aria-invalid"];
  if (!mode) {
    return (
      <input
        {...props}
        aria-invalid={ariaInvalid}
        className="input"
        inputMode={inputMode}
        step={step}
        type={type}
      />
    );
  }
  return (
    <DigitInput
      {...props}
      aria-invalid={ariaInvalid}
      className="input"
      mode={mode}
    />
  );
}

export function SelectInput(
  props: SelectHTMLAttributes<HTMLSelectElement> & { error?: string },
) {
  const { error, ...rest } = props;
  return (
    <select
      {...rest}
      aria-invalid={error ? true : undefined}
      className="input"
    />
  );
}

export function CommandForm({
  title,
  pending,
  error,
  confirmLabel,
  onSubmit,
  onCancel,
  children,
}: {
  title: string;
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
      onClose={onCancel}
      onSubmit={submit}
      pending={pending}
      title={title}
    >
      <Alert message={error} />
      {children}
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

export function ReasonField({
  label,
  name = "reason",
  error,
  required = true,
}: {
  label: string;
  name?: string;
  error?: string | undefined;
  required?: boolean;
}) {
  return (
    <Field error={error} label={label} required={required}>
      <textarea className="input min-h-24" name={name} required={required} />
    </Field>
  );
}

export function ServerFacts({
  rows,
}: {
  rows: Array<{ label: string; value: string }>;
}) {
  const tx = useTx();
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-sm text-slate-500">{tx(row.label)}</dt>
          <dd className="font-medium">
            <IdentifierValue value={row.value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

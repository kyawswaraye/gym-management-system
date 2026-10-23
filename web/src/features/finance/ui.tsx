"use client";

import type {
  FormEvent,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

import { actionButtonClass } from "@/lib/ui/action-button";
import { IdentifierValue } from "@/lib/ui/identifier";

export function Alert({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="error" role="alert">
      {message}
    </p>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function Empty({ label }: { label: string }) {
  return <p className="muted">{label}</p>;
}

export function StatusText({ status }: { status: string }) {
  return (
    <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold">
      {status}
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
  const showPrevious = index > 0;
  const showNext = hasMore;
  if (!showPrevious && !showNext && !bounded) return null;

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4"
    >
      <div className="flex items-center gap-2">
        {showPrevious && (
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 shadow-sm hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={pending}
            onClick={onPrevious}
            type="button"
          >
            <span aria-hidden="true">←</span>
            Previous page
          </button>
        )}
        {showNext && (
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-slate-800 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={pending}
            onClick={onNext}
            type="button"
          >
            Next page
            <span aria-hidden="true">→</span>
          </button>
        )}
        {(showPrevious || showNext) && (
          <span className="ml-1 text-xs font-medium text-slate-500">
            Page {index + 1}
          </span>
        )}
      </div>
      {bounded && (
        <p className="text-sm text-amber-700">
          Refine the filters to see older rows.
        </p>
      )}
    </nav>
  );
}

export function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string | undefined;
  children: ReactNode;
}) {
  return (
    <label className="field">
      {label}
      {children}
      {error && <span className="text-sm text-red-700">{error}</span>}
    </label>
  );
}

export function TextInput({
  error,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { error?: string }) {
  return (
    <input
      {...props}
      aria-invalid={error ? true : undefined}
      className="input"
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
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(new FormData(event.currentTarget));
  }

  return (
    <form
      aria-busy={pending}
      className="grid gap-3 rounded-lg border border-slate-200 p-4"
      onSubmit={submit}
    >
      <h3 className="text-base font-semibold">{title}</h3>
      <Alert message={error} />
      {children}
      <div className="flex flex-wrap gap-3">
        <button
          className={actionButtonClass(confirmLabel)}
          disabled={pending}
          type="submit"
        >
          {confirmLabel}
        </button>
        <button
          className="underline tone-cancel"
          disabled={pending}
          onClick={onCancel}
          type="button"
        >
          Back
        </button>
      </div>
    </form>
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
    <Field error={error} label={label}>
      <textarea className="input min-h-24" name={name} required={required} />
    </Field>
  );
}

export function ServerFacts({
  rows,
}: {
  rows: Array<{ label: string; value: string }>;
}) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-sm text-slate-500">{row.label}</dt>
          <dd className="font-medium">
            <IdentifierValue value={row.value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

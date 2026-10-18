"use client";

import type {
  FormEvent,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

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
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        className="underline"
        disabled={pending || index === 0}
        onClick={onPrevious}
        type="button"
      >
        Previous page
      </button>
      <button
        className="underline"
        disabled={pending || !hasMore}
        onClick={onNext}
        type="button"
      >
        Next page
      </button>
      {bounded && (
        <p className="muted text-sm">Refine the filters to see older rows.</p>
      )}
    </div>
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
      className="grid gap-3 rounded-lg border border-slate-200 p-4"
      onSubmit={submit}
    >
      <h3 className="text-base font-semibold">{title}</h3>
      <Alert message={error} />
      {children}
      <div className="flex flex-wrap gap-3">
        <button className="button" disabled={pending} type="submit">
          {confirmLabel}
        </button>
        <button
          className="underline"
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
}: {
  label: string;
  name?: string;
  error?: string | undefined;
}) {
  return (
    <Field error={error} label={label}>
      <textarea className="input min-h-24" name={name} required />
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
          <dd className="font-medium">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

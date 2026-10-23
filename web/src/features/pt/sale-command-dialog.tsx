"use client";

import type { FormEvent, ReactNode, Ref } from "react";

import { Alert } from "@/features/finance/ui";

export function SaleCommandDialog({
  title,
  titleId,
  description,
  descriptionId,
  dialogId,
  pending,
  error,
  submitLabel,
  submitClass,
  submitDisabled = false,
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
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(event);
        }}
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

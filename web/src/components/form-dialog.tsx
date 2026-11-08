"use client";
import { useTx } from "@/i18n/use-tx";

import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useRef,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";

import { LoadingMark } from "@/components/loading-mark";

function dialogWidth(wide: boolean, compact: boolean): string {
  if (compact) return "sm:max-w-lg";
  if (wide) return "sm:max-w-3xl";
  return "sm:max-w-2xl";
}

function isActionRow(node: ReactNode): boolean {
  if (!isValidElement<{ children?: ReactNode }>(node)) return false;
  const kids = Children.toArray(node.props.children).filter(
    (child) => typeof child !== "string" || child.trim() !== "",
  );
  return (
    kids.length > 0 &&
    kids.every((child) => isValidElement(child) && child.type === "button")
  );
}

export function DialogFrame({
  title,
  description,
  pending = false,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  description?: string | undefined;
  pending?: boolean;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const tx = useTx();
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget || pending) return;
        onClose();
      }}
      role="presentation"
    >
      <div
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className={`flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] ${
          wide ? "sm:max-w-3xl" : "sm:max-w-2xl"
        }`}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || pending) return;
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
        ref={panelRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {tx(title)}
            </h2>
            {description ? (
              <p className="mt-1 text-sm text-slate-500" id={descriptionId}>
                {tx(description)}
              </p>
            ) : null}
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
        <div className="grid gap-4 px-5 py-4 sm:px-6">{children}</div>
      </div>
    </div>
  );
}

export function FormDialog({
  title,
  description,
  pending = false,
  pendingLabel = "Saving…",
  onClose,
  onSubmit,
  children,
  wide = false,
  compact = false,
  noValidate = false,
  formRef,
}: {
  title: string;
  description?: string | undefined;
  pending?: boolean;
  pendingLabel?: string;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
  children: ReactNode;
  wide?: boolean;
  compact?: boolean;
  noValidate?: boolean;
  formRef?: Ref<HTMLFormElement>;
}) {
  const tx = useTx();
  const titleId = useId();
  const descriptionId = useId();
  const localRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const node =
      formRef && typeof formRef !== "function"
        ? formRef.current
        : localRef.current;
    node?.focus({ preventScroll: true });
  }, [formRef]);

  const items = Children.toArray(children);
  const last = items.at(-1);
  const pinned = last && isActionRow(last) ? last : null;
  const body = pinned ? items.slice(0, -1) : items;

  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget || pending) return;
        onClose();
      }}
      role="presentation"
    >
      <form
        aria-busy={pending || undefined}
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className={`dialog-panel flex h-full max-h-full min-h-0 w-full flex-col overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] ${dialogWidth(wide, compact)}`}
        noValidate={noValidate || undefined}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || pending) return;
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
        onSubmit={onSubmit}
        ref={(node) => {
          localRef.current = node;
          if (typeof formRef === "function") formRef(node);
          else if (formRef) formRef.current = node;
        }}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {tx(title)}
            </h2>
            {description ? (
              <p className="mt-1 text-sm text-slate-500" id={descriptionId}>
                {tx(description)}
              </p>
            ) : null}
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
        <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto px-5 py-4 sm:px-6">
          {pending ? (
            <p
              aria-live="polite"
              className="flex items-center gap-2 text-sm font-medium text-slate-600"
            >
              <LoadingMark className="size-4" />
              {tx(pendingLabel)}
            </p>
          ) : null}
          {body}
        </div>
        {pinned ? (
          <div className="sticky bottom-0 z-10 border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
            {pinned}
          </div>
        ) : null}
      </form>
    </div>
  );
}

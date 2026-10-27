"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";

import { formatAppDate } from "@/lib/date";
import {
  ASSIGNED_AFTER_APPROVAL,
  DRAFT_NOTICE,
  RECEIPT_AUTO_PRINT_DELAY_MS,
  RECEIPT_PAGE_CSS,
  RECEIPT_SLIP_CSS,
  TEST_NOTICE,
  TEST_RECEIPT_NUMBER,
  receiptPrintHref,
  safeReceiptLogoSrc,
  type ReceiptPreviewContent,
  type ReceiptPreviewMode,
} from "@/features/finance/receipt-print";
import { actionButtonClass } from "@/lib/ui/action-button";

function leavePrintPage() {
  window.close();
  if (window.closed) return;
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  window.location.assign("/payments");
}

function pretty(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function RequiredPhrase({ phrase }: { phrase: string }) {
  const tx = useTx();
  const translated = tx(phrase);
  return (
    <>
      <span className="receipt-required-phrase">{phrase}</span>
      {translated !== phrase ? (
        <span className="receipt-phrase-alt">{translated}</span>
      ) : null}
    </>
  );
}

function formatSlipAmount(amount: string): string {
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(amount.trim());
  if (!match) return amount;
  const grouped = (match[2] ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${match[1] ?? ""}${grouped}${match[3] ?? ""}`;
}

function toCents(value: string): number | null {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const whole = Number(match[2]);
  const fraction = Number((match[3] ?? "").padEnd(2, "0"));
  if (!Number.isSafeInteger(whole) || !Number.isSafeInteger(fraction))
    return null;
  const sign = match[1] === "-" ? -1 : 1;
  return sign * (whole * 100 + fraction);
}

function fromCents(value: number): string {
  const sign = value < 0 ? "-" : "";
  const absolute = Math.abs(value);
  return `${sign}${Math.trunc(absolute / 100)}.${String(absolute % 100).padStart(2, "0")}`;
}

function slipLines(model: ReceiptPreviewContent) {
  if (model.lines && model.lines.length > 0) return model.lines;
  if (!model.description && !model.amount) return [];
  return [{ description: model.description, amount: model.amount }];
}

function slipTotals(model: ReceiptPreviewContent) {
  const lines = slipLines(model);
  let subtotalCents = 0;
  for (const line of lines) {
    const cents = toCents(line.amount);
    if (cents === null) {
      return {
        lines,
        subtotal: model.amount,
        discount: model.discount ?? "0.00",
        total: model.amount,
        paid: model.paid ?? model.amount,
      };
    }
    subtotalCents += cents;
  }
  const discountCents = toCents(model.discount ?? "0.00") ?? 0;
  const totalCents = subtotalCents - discountCents;
  const total = fromCents(totalCents);
  return {
    lines,
    subtotal: fromCents(subtotalCents),
    discount: model.discount ?? "0.00",
    total,
    paid: model.paid ?? total,
  };
}

function Row({ label, value }: { label: string; value: string }) {
  const tx = useTx();
  if (!value) return null;
  return (
    <p className="receipt-row">
      <span className="receipt-label">{tx(label)}</span>
      <span className="receipt-value">{value}</span>
    </p>
  );
}

function StatusAlert({ code }: { code: "VOID" | "REPLACED" }) {
  const tx = useTx();
  const translated = tx(code);
  return (
    <p className="receipt-status-alert" role="status">
      <span className="receipt-status-code">{code}</span>
      {translated !== code ? (
        <span className="receipt-phrase-alt">{translated}</span>
      ) : null}
    </p>
  );
}

export function ReceiptPreview({ model }: { model: ReceiptPreviewContent }) {
  const tx = useTx();
  const logoSrc = safeReceiptLogoSrc(model.logoUrl);
  const prominent =
    model.status === "VOID" || model.status === "REPLACED" ? model.status : "";
  const receiptNumber =
    model.mode === "test" ? TEST_RECEIPT_NUMBER : model.receiptNumber.trim();
  const totals = slipTotals(model);

  return (
    <article className="receipt-sheet" data-receipt-mode={model.mode}>
      <style>{RECEIPT_SLIP_CSS}</style>
      {logoSrc ? <img alt="" className="receipt-logo" src={logoSrc} /> : null}
      <h1 className="receipt-heading">{model.gymName}</h1>
      {model.address ? (
        <p className="receipt-center receipt-pre">{model.address}</p>
      ) : null}
      {model.phone ? (
        <p className="receipt-center">
          {tx("Phone")} <span>{model.phone}</span>
        </p>
      ) : null}
      <hr className="receipt-rule" />
      {model.mode === "draft" ? (
        <p className="receipt-banner" role="status">
          <RequiredPhrase phrase={DRAFT_NOTICE} />
        </p>
      ) : null}
      {model.mode === "test" ? (
        <>
          <p className="receipt-banner" role="status">
            <RequiredPhrase phrase={TEST_NOTICE} />
          </p>
          <p className="receipt-note">
            {tx("This slip is a printer test. It is not a receipt.")}
          </p>
        </>
      ) : null}
      {prominent ? <StatusAlert code={prominent} /> : null}
      {model.mode !== "final" || prominent ? (
        <hr className="receipt-rule" />
      ) : null}
      <p className="receipt-row">
        <span className="receipt-label">{tx("Receipt number")}</span>
        <span className="receipt-value">
          {model.mode === "draft" ? (
            <RequiredPhrase phrase={ASSIGNED_AFTER_APPROVAL} />
          ) : (
            receiptNumber
          )}
        </span>
      </p>
      <p className="receipt-row">
        <span className="receipt-label">{tx("Issued")}</span>
        <span className="receipt-value">
          {model.mode === "draft" ? (
            <RequiredPhrase phrase={ASSIGNED_AFTER_APPROVAL} />
          ) : (
            model.issuedAt
          )}
        </span>
      </p>
      <Row label="Business date" value={formatAppDate(model.businessDate)} />
      <Row label="Member" value={model.memberName} />
      <Row label="Member number" value={model.memberNumber} />
      <hr className="receipt-rule" />
      {totals.lines.map((line, index) => (
        <p className="receipt-row" key={`${line.description}-${index}`}>
          <span className="receipt-item">{line.description}</span>
          <span className="receipt-value">{formatSlipAmount(line.amount)}</span>
        </p>
      ))}
      <hr className="receipt-rule" />
      <Row label="Subtotal" value={formatSlipAmount(totals.subtotal)} />
      <Row label="Discount" value={formatSlipAmount(totals.discount)} />
      <p className="receipt-row receipt-total">
        <span className="receipt-label">{tx("Total")}</span>
        <span className="receipt-value">
          {formatSlipAmount(totals.total)} {model.currency}
        </span>
      </p>
      <Row label="Paid" value={formatSlipAmount(totals.paid)} />
      <hr className="receipt-rule" />
      <Row label="Payment method" value={tx(pretty(model.method))} />
      <Row label="Issuer" value={model.issuerName} />
      {prominent ? null : (
        <Row label="Status" value={tx(pretty(model.status))} />
      )}
      {model.footer ? (
        <>
          <hr className="receipt-rule" />
          <p className="receipt-footer">{model.footer}</p>
        </>
      ) : null}
      <div aria-hidden="true" className="receipt-cut" />
    </article>
  );
}

export function ReceiptPrintFrame({
  autoPrint = false,
  children,
  mode,
  printLabel,
}: {
  autoPrint?: boolean;
  children: ReactNode;
  mode: ReceiptPreviewMode;
  printLabel: string;
}) {
  const tx = useTx();
  const allowAutoPrint = autoPrint && mode !== "draft";

  useEffect(() => {
    if (!allowAutoPrint) return;
    const id = window.setTimeout(() => {
      window.print();
    }, RECEIPT_AUTO_PRINT_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [allowAutoPrint]);

  return (
    <div
      className="receipt-print-stack"
      data-auto-print={allowAutoPrint ? "true" : "false"}
    >
      <style>{RECEIPT_PAGE_CSS}</style>
      {children}
      <div
        aria-label={tx("Receipt print")}
        className="receipt-print-controls"
        role="group"
      >
        <button
          className={actionButtonClass(printLabel)}
          onClick={() => window.print()}
          type="button"
        >
          {tx(printLabel)}
        </button>
        <button
          className={actionButtonClass("Close")}
          onClick={leavePrintPage}
          type="button"
        >
          {tx("Close")}
        </button>
      </div>
    </div>
  );
}

export function ReceiptPrintLink({
  receiptId,
  className = "button",
}: {
  receiptId: string | null | undefined;
  className?: string;
}) {
  const tx = useTx();
  if (!receiptId) return null;
  const href = receiptPrintHref(receiptId);
  if (!href) return null;
  return (
    <Link
      className={className}
      href={href}
      rel="noopener noreferrer"
      target="_blank"
    >
      {tx("Print receipt")}
    </Link>
  );
}

export function ReceiptPrintFallback({ message }: { message: string }) {
  const tx = useTx();
  return (
    <ReceiptPrintFrame
      autoPrint={false}
      mode="final"
      printLabel="Print receipt"
    >
      <p role="alert">{tx(message)}</p>
    </ReceiptPrintFrame>
  );
}

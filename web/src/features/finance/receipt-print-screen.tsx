"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState } from "react";

import {
  getReceiptPrint,
  getReceiptPrintContext,
} from "@/features/finance/client";
import {
  ReceiptPreview,
  ReceiptPrintFrame,
} from "@/features/finance/receipt-preview";
import {
  finalReceiptPreview,
  testReceiptPreview,
  type ReceiptPreviewContent,
} from "@/features/finance/receipt-print";
import { OperationError } from "@/features/operations/client";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      model: ReceiptPreviewContent;
      printerEnabled: boolean;
    };

export function FinalReceiptPrint({
  autoPrint = false,
  receiptId,
  timeZone,
}: {
  autoPrint?: boolean;
  receiptId: string;
  timeZone: string;
}) {
  const tx = useTx();
  const state = useLoadedReceipt(receiptId, timeZone);

  if (state.status === "loading") {
    return (
      <ReceiptPrintFrame
        autoPrint={false}
        mode="final"
        printLabel="Print receipt"
      >
        <p role="status">{tx("Loading the receipt.")}</p>
      </ReceiptPrintFrame>
    );
  }
  if (state.status === "error") {
    return (
      <ReceiptPrintFrame
        autoPrint={false}
        mode="final"
        printLabel="Print receipt"
      >
        <p role="alert">{tx(state.message)}</p>
      </ReceiptPrintFrame>
    );
  }
  return (
    <ReceiptPrintFrame
      autoPrint={autoPrint && state.printerEnabled}
      mode="final"
      printLabel="Print receipt"
    >
      <ReceiptPreview model={state.model} />
    </ReceiptPrintFrame>
  );
}

export function TestReceiptPrint({
  autoPrint = false,
  currency,
  gymName,
}: {
  autoPrint?: boolean;
  currency: string;
  gymName: string;
}) {
  const tx = useTx();
  const state = useLoadedTest(currency, gymName);

  if (state.status === "loading") {
    return (
      <ReceiptPrintFrame
        autoPrint={false}
        mode="test"
        printLabel="Print test slip"
      >
        <p role="status">{tx("Loading the printer test.")}</p>
      </ReceiptPrintFrame>
    );
  }
  if (state.status === "error") {
    return (
      <ReceiptPrintFrame
        autoPrint={false}
        mode="test"
        printLabel="Print test slip"
      >
        <p role="alert">{tx(state.message)}</p>
      </ReceiptPrintFrame>
    );
  }
  return (
    <ReceiptPrintFrame
      autoPrint={autoPrint}
      mode="test"
      printLabel="Print test slip"
    >
      <ReceiptPreview model={state.model} />
    </ReceiptPrintFrame>
  );
}

function useLoadedReceipt(receiptId: string, timeZone: string): LoadState {
  const requestKey = `${receiptId}\n${timeZone}`;
  const [loaded, setLoaded] = useState<{
    key: string;
    state: LoadState;
  } | null>(null);

  useEffect(() => {
    let live = true;
    getReceiptPrint(receiptId)
      .then((payload) => {
        if (!live) return;
        setLoaded({
          key: requestKey,
          state: {
            status: "ready",
            model: finalReceiptPreview(payload, timeZone),
            printerEnabled: payload.printer_enabled,
          },
        });
      })
      .catch((error: unknown) => {
        if (!live) return;
        setLoaded({
          key: requestKey,
          state: {
            status: "error",
            message: failureMessage(error, "The receipt could not be printed."),
          },
        });
      });
    return () => {
      live = false;
    };
  }, [receiptId, requestKey, timeZone]);

  return loaded?.key === requestKey ? loaded.state : { status: "loading" };
}

function useLoadedTest(currency: string, gymName: string): LoadState {
  const requestKey = `${currency}\n${gymName}`;
  const [loaded, setLoaded] = useState<{
    key: string;
    state: LoadState;
  } | null>(null);

  useEffect(() => {
    let live = true;
    getReceiptPrintContext()
      .then((context) => {
        if (!live) return;
        setLoaded({
          key: requestKey,
          state: {
            status: "ready",
            model: testReceiptPreview(context, { name: gymName, currency }),
            printerEnabled: context.printer_enabled,
          },
        });
      })
      .catch((error: unknown) => {
        if (!live) return;
        setLoaded({
          key: requestKey,
          state: {
            status: "error",
            message: failureMessage(
              error,
              "The printer setup could not be loaded.",
            ),
          },
        });
      });
    return () => {
      live = false;
    };
  }, [currency, gymName, requestKey]);

  return loaded?.key === requestKey ? loaded.state : { status: "loading" };
}

function failureMessage(error: unknown, fallback: string): string {
  return error instanceof OperationError && error.message
    ? error.message
    : fallback;
}

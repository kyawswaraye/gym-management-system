import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getReceiptPrint,
  getReceiptPrintContext,
} from "@/features/finance/client";
import {
  FinalReceiptPrint,
  TestReceiptPrint,
} from "@/features/finance/receipt-print-screen";
import { OperationError } from "@/features/operations/client";
import type { ReceiptPrint, ReceiptPrintContext } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  getReceiptPrint: vi.fn(),
  getReceiptPrintContext: vi.fn(),
}));

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";
const loadReceipt = vi.mocked(getReceiptPrint);
const loadContext = vi.mocked(getReceiptPrintContext);

function payload(overrides: Partial<ReceiptPrint> = {}): ReceiptPrint {
  return {
    id: ID,
    payment_id: "11111111-1111-4111-8111-111111111111",
    receipt_number: "RCP-100",
    issued_at: "2026-01-15T02:30:00.000Z",
    gym_code: "SECRET-CODE",
    gym_name: "Yangon Fitness",
    gym_address: "123 Strand",
    member_number: "M-100",
    member_name: "Ma Hla",
    description: "Membership",
    amount: "15000.00",
    currency: "MMK",
    method: "BANK_TRANSFER",
    business_date: "2026-01-15",
    issuer_name: "Ada",
    status: "REPLACED",
    created_at: "1999-12-31T00:00:00.000Z",
    phone: "0911111111",
    logo: null,
    printer_enabled: true,
    printer_print_logo: false,
    printer_print_address: true,
    printer_print_phone: false,
    printer_footer: "See you again",
    ...overrides,
  };
}

function context(
  overrides: Partial<ReceiptPrintContext> = {},
): ReceiptPrintContext {
  return {
    printer_enabled: true,
    printer_print_logo: false,
    printer_print_address: true,
    printer_print_phone: true,
    printer_footer: "ကျေးဇူးတင်ပါသည်",
    phone: "09123456789",
    logo: null,
    ...overrides,
  };
}

describe("receipt print screens", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.print = vi.fn();
    window.close = vi.fn();
  });

  it("renders the final payload and leaves auto print off until requested", async () => {
    loadReceipt.mockResolvedValue(payload());
    const view = render(
      <FinalReceiptPrint
        autoPrint={false}
        receiptId={ID}
        timeZone="Asia/Yangon"
      />,
    );
    expect(await screen.findByText("RCP-100")).toBeInTheDocument();
    expect(screen.getByText("REPLACED")).toBeInTheDocument();
    expect(screen.getByText("123 Strand")).toBeInTheDocument();
    expect(screen.queryByText("0911111111")).not.toBeInTheDocument();
    expect(screen.queryByText("SECRET-CODE")).not.toBeInTheDocument();
    expect(screen.queryByText(payload().payment_id)).not.toBeInTheDocument();
    expect(view.container.textContent).not.toMatch(/printer_|1999-12-31/);
    expect(
      view.container.querySelector("[data-auto-print='false']"),
    ).not.toBeNull();
    expect(loadContext).not.toHaveBeenCalled();
    expect(window.print).not.toHaveBeenCalled();

    view.rerender(
      <FinalReceiptPrint autoPrint receiptId={ID} timeZone="Asia/Yangon" />,
    );
    expect(await screen.findByText("RCP-100")).toBeInTheDocument();
    expect(
      view.container.querySelector("[data-auto-print='true']"),
    ).not.toBeNull();
  });

  it("does not auto-print a final receipt when the printer is disabled", async () => {
    loadReceipt.mockResolvedValue(payload({ printer_enabled: false }));
    const view = render(
      <FinalReceiptPrint autoPrint receiptId={ID} timeZone="Asia/Yangon" />,
    );
    expect(await screen.findByText("RCP-100")).toBeInTheDocument();
    expect(
      view.container.querySelector("[data-auto-print='false']"),
    ).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "Print receipt" }),
    ).toBeInTheDocument();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("shows an API error without printing", async () => {
    loadReceipt.mockRejectedValue(new OperationError("Receipt was not found."));
    render(
      <FinalReceiptPrint autoPrint receiptId={ID} timeZone="Asia/Yangon" />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Receipt was not found.",
    );
    expect(document.querySelector("[data-auto-print='true']")).toBeNull();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("builds the test slip from print context and sample copy", async () => {
    loadContext.mockResolvedValue(context());
    const view = render(
      <TestReceiptPrint autoPrint currency="MMK" gymName="Yangon Fitness" />,
    );
    expect(await screen.findByText("TEST")).toBeInTheDocument();
    expect(screen.getByText("TEST — not a receipt")).toBeInTheDocument();
    expect(screen.getByText(/မလှ/)).toBeInTheDocument();
    expect(screen.getByText(/အသင်းဝင်ကြေး/)).toBeInTheDocument();
    expect(screen.getByText("09123456789")).toBeInTheDocument();
    expect(screen.getByText("ကျေးဇူးတင်ပါသည်")).toBeInTheDocument();
    expect(view.container.textContent).not.toMatch(/printer_/);
    expect(loadReceipt).not.toHaveBeenCalled();
    expect(
      view.container.querySelector("[data-auto-print='true']"),
    ).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "Print test slip" }),
    ).toBeInTheDocument();
  });

  it("still auto-prints a test slip when the gym printer is turned off", async () => {
    loadContext.mockResolvedValue(context({ printer_enabled: false }));
    const view = render(
      <TestReceiptPrint autoPrint currency="MMK" gymName="Yangon Fitness" />,
    );
    expect(await screen.findByText("TEST")).toBeInTheDocument();
    expect(
      view.container.querySelector("[data-auto-print='true']"),
    ).not.toBeNull();
  });
});

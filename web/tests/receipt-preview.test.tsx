import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ReceiptPreview,
  ReceiptPrintFrame,
} from "@/features/finance/receipt-preview";
import {
  ASSIGNED_AFTER_APPROVAL,
  DRAFT_NOTICE,
  RECEIPT_AUTO_PRINT_DELAY_MS,
  RECEIPT_PAGE_CSS,
  RECEIPT_SLIP_CSS,
  TEST_NOTICE,
  draftReceiptPreview,
  finalReceiptPreview,
  receiptAutoPrintEnabled,
  receiptLogoDataUrl,
  receiptPrintHref,
  receiptTestPrintHref,
  testReceiptPreview,
  type ReceiptPreviewContent,
} from "@/features/finance/receipt-print";
import { LocaleProvider } from "@/i18n/use-tx";
import type { ReceiptPrint, ReceiptPrintLogo } from "@/types/api";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";

function pngLogo(): ReceiptPrintLogo {
  return {
    content_type: "image/png",
    data_base64: Buffer.from([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00,
    ]).toString("base64"),
  };
}

function content(
  overrides: Partial<ReceiptPreviewContent> = {},
): ReceiptPreviewContent {
  return {
    mode: "final",
    gymName: "Yangon Fitness",
    address: "123 Strand",
    phone: "0911111111",
    logoUrl: receiptLogoDataUrl(pngLogo()),
    footer: "Thank you\nကျေးဇူးတင်ပါသည်",
    receiptNumber: "RCP-100",
    issuedAt: "15 Jan 2026, 09:00",
    businessDate: "2026-01-15",
    memberName: "Ma Hla",
    memberNumber: "M-100",
    description: "Membership",
    amount: "15000.00",
    currency: "MMK",
    method: "CASH",
    issuerName: "Ada",
    status: "ISSUED",
    ...overrides,
  };
}

function printPayload(overrides: Partial<ReceiptPrint> = {}): ReceiptPrint {
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
    description: "Membership <script>alert(1)</script>",
    amount: "15000.00",
    currency: "MMK",
    method: "CASH",
    business_date: "2026-01-15",
    issuer_name: "Ada",
    status: "ISSUED",
    created_at: "1999-12-31T00:00:00.000Z",
    phone: "0911111111",
    logo: pngLogo(),
    printer_enabled: true,
    printer_print_logo: true,
    printer_print_address: true,
    printer_print_phone: true,
    printer_footer: "Thank you",
    ...overrides,
  };
}

describe("receipt logo data URLs", () => {
  it("accepts only whitelisted images whose bytes match the type", () => {
    const png = receiptLogoDataUrl(pngLogo());
    expect(png).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/);
    expect(
      receiptLogoDataUrl({
        content_type: "image/jpeg",
        data_base64: Buffer.from([0xff, 0xd8, 0xff, 0x00]).toString("base64"),
      }),
    ).toMatch(/^data:image\/jpeg;base64,/);
    expect(
      receiptLogoDataUrl({
        content_type: "image/webp",
        data_base64: Buffer.from([
          0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50,
        ]).toString("base64"),
      }),
    ).toMatch(/^data:image\/webp;base64,/);
    expect(
      receiptLogoDataUrl({
        content_type: "image/svg+xml",
        data_base64: Buffer.from("<svg>").toString("base64"),
      }),
    ).toBeNull();
    expect(
      receiptLogoDataUrl({
        content_type: "text/html",
        data_base64: Buffer.from("<script>").toString("base64"),
      }),
    ).toBeNull();
    expect(
      receiptLogoDataUrl({
        content_type: "image/png",
        data_base64: Buffer.from("<script>").toString("base64"),
      }),
    ).toBeNull();
    expect(
      receiptLogoDataUrl({
        content_type: "image/png",
        data_base64: `${pngLogo().data_base64}\n`,
      }),
    ).toBeNull();
    expect(receiptLogoDataUrl(null)).toBeNull();
  });
});

describe("receipt preview copy", () => {
  it("keeps the 80mm slip styles separate from the print page", () => {
    expect(RECEIPT_SLIP_CSS).toMatch(/width:\s*80mm/);
    expect(RECEIPT_SLIP_CSS).toMatch(/--font-myanmar/);
    expect(RECEIPT_SLIP_CSS).not.toMatch(/@page/);
    expect(RECEIPT_PAGE_CSS).toMatch(/@page\s*\{[^}]*size:\s*80mm\s+auto/);
    expect(RECEIPT_PAGE_CSS).toMatch(/@page\s*\{[^}]*margin:\s*0/);
    expect(RECEIPT_PAGE_CSS).toMatch(/width:\s*72mm/);
    expect(RECEIPT_PAGE_CSS).not.toMatch(/A4|letter|210mm/);
    expect(RECEIPT_PAGE_CSS).toMatch(/@media print/);
    expect(RECEIPT_PAGE_CSS).toMatch(
      /\.receipt-print-controls\s*\{[^}]*display:\s*none/,
    );
    expect(RECEIPT_PAGE_CSS).toMatch(
      /\.receipt-cut\s*\{[^}]*height:\s*18mm[^}]*break-after:\s*page/,
    );
    expect(RECEIPT_SLIP_CSS).toMatch(/\.receipt-cut\s*\{[^}]*display:\s*none/);
  });

  it("builds draft, final, and test slips without printer settings", () => {
    const draft = draftReceiptPreview({
      gymName: "Yangon Fitness",
      address: "123 Strand",
      phone: "0911111111",
      showAddress: false,
      showPhone: true,
      showLogo: true,
      logo: { content_type: "image/svg+xml", data_base64: "PHN2Zz4=" },
      footer: "See you",
      businessDate: "2026-01-15",
      memberName: "Ma Hla",
      memberNumber: "M-100",
      description: "Membership",
      amount: "15000.00",
      currency: "MMK",
      method: "CASH",
      issuerName: "Ada",
    });
    expect(draft.mode).toBe("draft");
    expect(draft.receiptNumber).toBe("");
    expect(draft.address).toBe("");
    expect(draft.phone).toBe("0911111111");
    expect(draft.logoUrl).toBeNull();

    const finalModel = finalReceiptPreview(
      printPayload({
        printer_print_address: false,
        printer_print_phone: false,
        printer_print_logo: false,
      }),
      "Asia/Yangon",
    );
    expect(Object.keys(finalModel).sort()).toEqual([
      "address",
      "amount",
      "businessDate",
      "currency",
      "description",
      "footer",
      "gymName",
      "issuedAt",
      "issuerName",
      "logoUrl",
      "memberName",
      "memberNumber",
      "method",
      "mode",
      "phone",
      "receiptNumber",
      "status",
    ]);
    expect(finalModel.address).toBe("");
    expect(finalModel.phone).toBe("");
    expect(finalModel.logoUrl).toBeNull();
    expect(finalModel.receiptNumber).toBe("RCP-100");
    expect(JSON.stringify(finalModel)).not.toMatch(
      /printer_|SECRET-CODE|payment_id|1999-12-31/,
    );

    const testModel = testReceiptPreview(
      {
        printer_enabled: false,
        printer_print_logo: false,
        printer_print_address: true,
        printer_print_phone: false,
        printer_footer: "Footer",
        phone: "09999999999",
        logo: pngLogo(),
      },
      { name: "Yangon Fitness", currency: "MMK" },
    );
    expect(testModel.receiptNumber).toBe("TEST");
    expect(testModel.memberName).toContain("မလှ");
    expect(testModel.description).toContain("အသင်းဝင်ကြေး");
    expect(testModel.address).toContain("နမူနာလမ်း");
    expect(testModel.phone).toBe("");
    expect(testModel.logoUrl).toBeNull();
    expect(JSON.stringify(testModel)).not.toMatch(/printer_|09999999999/);
  });

  it("shows the draft and test notices and keeps real receipt numbers off those slips", () => {
    const { rerender } = render(
      <ReceiptPreview
        model={content({ mode: "draft", receiptNumber: "RCP-100" })}
      />,
    );
    expect(screen.getByText(DRAFT_NOTICE)).toBeInTheDocument();
    expect(screen.getAllByText(ASSIGNED_AFTER_APPROVAL)).toHaveLength(2);
    expect(screen.queryByText("RCP-100")).not.toBeInTheDocument();
    expect(screen.queryByText(TEST_NOTICE)).not.toBeInTheDocument();

    rerender(
      <ReceiptPreview
        model={content({ mode: "test", receiptNumber: "RCP-100" })}
      />,
    );
    expect(screen.getByText(TEST_NOTICE)).toBeInTheDocument();
    expect(screen.getByText("TEST")).toBeInTheDocument();
    expect(
      screen.getByText("This slip is a printer test. It is not a receipt."),
    ).toBeInTheDocument();
    expect(screen.queryByText("RCP-100")).not.toBeInTheDocument();
    expect(screen.queryByText(DRAFT_NOTICE)).not.toBeInTheDocument();
  });

  it("shows each line at receipt size and the pos totals", () => {
    render(
      <ReceiptPreview
        model={content({
          description: "Membership",
          amount: "16500.00",
          lines: [
            { description: "Membership", amount: "15000.00" },
            { description: "Towel", amount: "2000.00" },
          ],
          discount: "500.00",
          paid: "16500.00",
        })}
      />,
    );

    expect(screen.getByText("Membership")).toBeInTheDocument();
    expect(screen.getByText("Towel")).toBeInTheDocument();
    expect(screen.getByText("17,000.00")).toBeInTheDocument();
    expect(screen.getByText("500.00")).toBeInTheDocument();
    expect(screen.getByText("16,500.00 MMK")).toBeInTheDocument();
    expect(screen.getAllByText("16,500.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Subtotal")).toBeInTheDocument();
    expect(screen.getByText("Discount")).toBeInTheDocument();
    expect(screen.getByText("Total")).toBeInTheDocument();
    expect(screen.getByText("Paid")).toBeInTheDocument();
    expect(document.querySelector(".receipt-amount")).toBeNull();
  });

  it("renders a final slip with Myanmar text, a safe logo, and prominent void status", () => {
    const { container, rerender } = render(
      <LocaleProvider locale="mm">
        <ReceiptPreview
          model={content({
            description: "Membership <script>alert(1)</script>",
            status: "VOID",
          })}
        />
      </LocaleProvider>,
    );

    expect(
      screen.getByRole("heading", { name: "Yangon Fitness" }),
    ).toBeInTheDocument();
    expect(screen.getByText("RCP-100")).toBeInTheDocument();
    expect(screen.getByText("15,000.00 MMK")).toBeInTheDocument();
    expect(screen.getByText("VOID")).toBeInTheDocument();
    expect(screen.getByText("ပယ်ဖျက်")).toBeInTheDocument();
    expect(container.querySelector(".receipt-status-alert")).not.toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(
      screen.getByText("Membership <script>alert(1)</script>"),
    ).toBeInTheDocument();
    const logo = container.querySelector("img");
    expect(logo?.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
    expect(logo?.getAttribute("src")).not.toMatch(/javascript:|[<>"']/);
    expect(container.querySelector(".receipt-footer")?.textContent).toBe(
      "Thank you\nကျေးဇူးတင်ပါသည်",
    );
    expect(container.querySelector(".receipt-cut")).not.toBeNull();
    expect(
      [...container.querySelectorAll("style")]
        .map((node) => node.textContent)
        .join(""),
    ).not.toMatch(/@page/);

    rerender(
      <ReceiptPreview
        model={content({
          logoUrl: "javascript:alert(1)",
          status: "REPLACED",
          mode: "final",
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("REPLACED")).toBeInTheDocument();
  });
});

describe("receipt print paths", () => {
  it("opens final and test print only through an explicit autoPrint query", () => {
    expect(receiptPrintHref(ID)).toBe(`/receipts/${ID}/print`);
    expect(receiptPrintHref(ID.toUpperCase(), { autoPrint: true })).toBe(
      `/receipts/${ID}/print?autoPrint=1`,
    );
    expect(receiptPrintHref(`${ID}?next=/admin`)).toBeNull();
    expect(receiptTestPrintHref()).toBe("/receipts/test-print");
    expect(receiptTestPrintHref({ autoPrint: true })).toBe(
      "/receipts/test-print?autoPrint=1",
    );
    expect(receiptAutoPrintEnabled("1")).toBe(true);
    expect(receiptAutoPrintEnabled("true")).toBe(false);
    expect(receiptAutoPrintEnabled(["1"])).toBe(false);
    expect(receiptAutoPrintEnabled(undefined)).toBe(false);
  });
});

describe("receipt print controls", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("prints from the button and from the explicit autoPrint prop, never for a draft", async () => {
    vi.useFakeTimers();
    const print = vi.fn();
    const close = vi.fn();
    window.print = print;
    window.close = close;

    const { rerender } = render(
      <ReceiptPrintFrame autoPrint mode="final" printLabel="Print receipt">
        <p>Ready</p>
      </ReceiptPrintFrame>,
    );
    expect(print).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECEIPT_AUTO_PRINT_DELAY_MS);
    });
    expect(print).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Print receipt" }));
    expect(print).toHaveBeenCalledTimes(2);
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    Object.defineProperty(window.history, "length", {
      configurable: true,
      value: 2,
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(back).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("link", { name: "Back to payments" }),
    ).not.toBeInTheDocument();
    expect(document.querySelector(".receipt-print-controls")).not.toBeNull();

    print.mockClear();
    rerender(
      <ReceiptPrintFrame autoPrint mode="draft" printLabel="Print receipt">
        <p>Draft</p>
      </ReceiptPrintFrame>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECEIPT_AUTO_PRINT_DELAY_MS);
    });
    expect(print).not.toHaveBeenCalled();
    expect(document.querySelector("[data-auto-print='false']")).not.toBeNull();
  });
});

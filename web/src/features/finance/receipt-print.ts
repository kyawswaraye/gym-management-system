import { formatInstant } from "@/lib/date";
import type {
  ReceiptPrint,
  ReceiptPrintContext,
  ReceiptPrintLogo,
} from "@/types/api";

export const DRAFT_NOTICE = "DRAFT — not a receipt";
export const TEST_NOTICE = "TEST — not a receipt";
export const ASSIGNED_AFTER_APPROVAL = "Assigned after approval";
export const TEST_RECEIPT_NUMBER = "TEST";
export const RECEIPT_AUTO_PRINT_PARAM = "autoPrint";
export const RECEIPT_AUTO_PRINT_DELAY_MS = 300;
const RECEIPT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LOGO_MIMES = {
  "image/jpeg": "image/jpeg",
  "image/png": "image/png",
  "image/webp": "image/webp",
} as const;
const MAX_LOGO_BYTES = 1_048_576;
const MAX_LOGO_BASE64 = 1_398_108;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;

type LogoMime = keyof typeof LOGO_MIMES;

export type ReceiptPreviewMode = "draft" | "final" | "test";

export type ReceiptPreviewContent = {
  mode: ReceiptPreviewMode;
  gymName: string;
  address: string;
  phone: string;
  logoUrl: string | null;
  footer: string;
  receiptNumber: string;
  issuedAt: string;
  businessDate: string;
  memberName: string;
  memberNumber: string;
  description: string;
  amount: string;
  lines?: { description: string; amount: string }[];
  discount?: string;
  paid?: string;
  currency: string;
  method: string;
  issuerName: string;
  status: string;
};

export type DraftReceiptInput = {
  gymName: string;
  address: string;
  phone: string;
  showAddress: boolean;
  showPhone: boolean;
  showLogo: boolean;
  logo: ReceiptPrintLogo | null;
  footer: string;
  businessDate: string;
  memberName: string;
  memberNumber: string;
  description: string;
  amount: string;
  currency: string;
  method: string;
  issuerName: string;
};

const TEST_SAMPLE = {
  address: "Sample Street, Yangon\nနမူနာလမ်း၊ ရန်ကုန်",
  memberName: "Ma Hla\nမလှ",
  memberNumber: "M-0000",
  description: "Membership fee\nအသင်းဝင်ကြေး",
  amount: "10000.00",
  method: "CASH",
  issuerName: "T-0001",
  status: "ISSUED",
  businessDate: "2026-01-15",
  issuedAt: "15-01-2026 09:00",
};

export const RECEIPT_SLIP_CSS = `
.receipt-sheet {
  box-sizing: border-box;
  width: 80mm;
  max-width: 100%;
  margin: 0 auto;
  padding: 5mm 4mm 6mm;
  background: #fff;
  color: #000;
  font-family: var(--font-myanmar), var(--font-latin), ui-sans-serif, sans-serif;
  font-size: 12px;
  line-height: 2;
}
.receipt-logo {
  display: block;
  width: auto;
  max-width: 22mm;
  max-height: 14mm;
  margin: 0 auto 2mm;
  object-fit: contain;
  filter: grayscale(1);
}
.receipt-heading {
  margin: 0;
  text-align: center;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
}
.receipt-center,
.receipt-footer,
.receipt-note {
  margin: 0.6mm 0 0;
  text-align: center;
  overflow-wrap: anywhere;
  font-size: 11px;
  line-height: 2;
}
.receipt-pre,
.receipt-value,
.receipt-item,
.receipt-footer {
  white-space: pre-line;
}
.receipt-banner,
.receipt-status-alert {
  margin: 2.5mm 0 0;
  padding: 1.2mm 2mm;
  text-align: center;
  font-weight: 700;
  line-height: 1.8;
}
.receipt-banner {
  border: 1px dashed #000;
  font-size: 12px;
  letter-spacing: 0.04em;
}
.receipt-status-alert {
  border: 2px solid #000;
  font-size: 16px;
}
.receipt-status-code,
.receipt-required-phrase,
.receipt-phrase-alt {
  display: block;
}
.receipt-status-code {
  letter-spacing: 0.12em;
}
.receipt-rule {
  border: 0;
  border-top: 1px dashed #000;
  margin: 2.2mm 0;
}
.receipt-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 3mm;
  margin: 0;
  font-size: 11px;
  line-height: 2;
}
.receipt-label {
  flex: 0 1 42%;
}
.receipt-value {
  min-width: 0;
  flex: 1 1 auto;
  text-align: right;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}
.receipt-item {
  margin: 0;
  flex: 1 1 auto;
  min-width: 0;
  text-align: left;
  font-size: 11px;
  font-weight: 400;
  line-height: 2;
  overflow-wrap: anywhere;
}
.receipt-total {
  font-weight: 700;
}
.receipt-footer {
  font-size: 12px;
  font-weight: 700;
}
.receipt-cut {
  display: none;
}
`;

export const RECEIPT_PAGE_CSS = `
.receipt-print-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: min(100%, 92mm);
}
.receipt-print-controls {
  display: flex;
  width: 80mm;
  max-width: 100%;
  gap: 10px;
  margin-top: 16px;
}
.receipt-print-controls .button {
  flex: 1 1 0;
  min-width: 0;
}
@page {
  size: 80mm auto;
  margin: 0;
}
@media print {
  html,
  body {
    width: 80mm;
    margin: 0 !important;
    background: #fff !important;
  }
  .receipt-print-workspace {
    display: block !important;
    min-height: 0 !important;
    padding: 0 !important;
    background: #fff !important;
  }
  .receipt-print-controls {
    display: none !important;
  }
  .receipt-sheet {
    width: 72mm;
    max-width: 72mm;
    margin: 0 auto;
    padding-bottom: 0 !important;
    box-shadow: none !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .receipt-cut {
    display: block !important;
    height: 18mm;
    break-after: page;
    page-break-after: always;
  }
}
`;

export function isReceiptId(value: string): boolean {
  return RECEIPT_ID.test(value.trim().toLowerCase());
}

export function receiptAutoPrintEnabled(
  value: string | string[] | undefined,
): boolean {
  return value === "1";
}

export function receiptPrintHref(
  receiptId: string,
  options?: { autoPrint?: boolean },
): string | null {
  const id = receiptId.trim().toLowerCase();
  if (!RECEIPT_ID.test(id)) return null;
  const query = options?.autoPrint ? `?${RECEIPT_AUTO_PRINT_PARAM}=1` : "";
  return `/receipts/${id}/print${query}`;
}

export function receiptTestPrintHref(options?: {
  autoPrint?: boolean;
}): string {
  const query = options?.autoPrint ? `?${RECEIPT_AUTO_PRINT_PARAM}=1` : "";
  return `/receipts/test-print${query}`;
}

export function receiptLogoDataUrl(
  logo: ReceiptPrintLogo | null | undefined,
): string | null {
  if (!logo) return null;
  const mime = LOGO_MIMES[logo.content_type as LogoMime];
  if (!mime) return null;
  const encoded = logo.data_base64;
  if (
    encoded.length === 0 ||
    encoded.length > MAX_LOGO_BASE64 ||
    encoded.length % 4 !== 0 ||
    !BASE64.test(encoded)
  ) {
    return null;
  }
  const bytes = decodeBase64(encoded);
  if (!bytes || bytes.length === 0 || bytes.length > MAX_LOGO_BYTES)
    return null;
  if (!matchesLogo(mime, bytes)) return null;
  return `data:${mime};base64,${encoded}`;
}

export function safeReceiptLogoSrc(url: string | null): string | null {
  if (!url) return null;
  const match =
    /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(url);
  if (!match?.[1] || !match[2]) return null;
  const safe = receiptLogoDataUrl({
    content_type: match[1],
    data_base64: match[2],
  });
  return safe === url ? safe : null;
}

export function draftReceiptPreview(
  input: DraftReceiptInput,
): ReceiptPreviewContent {
  return {
    mode: "draft",
    ...branding(input),
    receiptNumber: "",
    issuedAt: "",
    businessDate: input.businessDate,
    memberName: input.memberName,
    memberNumber: input.memberNumber,
    description: input.description,
    amount: input.amount,
    currency: input.currency,
    method: input.method,
    issuerName: input.issuerName,
    status: "DRAFT",
  };
}

export function finalReceiptPreview(
  payload: ReceiptPrint,
  timeZone: string,
): ReceiptPreviewContent {
  return {
    mode: "final",
    gymName: payload.gym_name,
    address: payload.printer_print_address ? payload.gym_address.trim() : "",
    phone: payload.printer_print_phone ? payload.phone.trim() : "",
    logoUrl: payload.printer_print_logo
      ? receiptLogoDataUrl(payload.logo)
      : null,
    footer: footerText(payload.printer_footer),
    receiptNumber: payload.receipt_number,
    issuedAt: formatInstant(payload.issued_at, timeZone),
    businessDate: payload.business_date,
    memberName: payload.member_name,
    memberNumber: payload.member_number,
    description: payload.description,
    amount: payload.amount,
    currency: payload.currency,
    method: payload.method,
    issuerName: payload.issuer_name,
    status: payload.status,
  };
}

export function testReceiptPreview(
  context: ReceiptPrintContext,
  gym: { name: string; currency: string },
): ReceiptPreviewContent {
  return {
    mode: "test",
    gymName: gym.name.trim(),
    address: context.printer_print_address ? TEST_SAMPLE.address : "",
    phone: context.printer_print_phone ? context.phone.trim() : "",
    logoUrl: context.printer_print_logo
      ? receiptLogoDataUrl(context.logo)
      : null,
    footer: footerText(context.printer_footer),
    receiptNumber: TEST_RECEIPT_NUMBER,
    issuedAt: TEST_SAMPLE.issuedAt,
    businessDate: TEST_SAMPLE.businessDate,
    memberName: TEST_SAMPLE.memberName,
    memberNumber: TEST_SAMPLE.memberNumber,
    description: TEST_SAMPLE.description,
    amount: TEST_SAMPLE.amount,
    currency: gym.currency,
    method: TEST_SAMPLE.method,
    issuerName: TEST_SAMPLE.issuerName,
    status: TEST_SAMPLE.status,
  };
}

function branding(input: DraftReceiptInput) {
  return {
    gymName: input.gymName.trim(),
    address: input.showAddress ? input.address.trim() : "",
    phone: input.showPhone ? input.phone.trim() : "",
    logoUrl: input.showLogo ? receiptLogoDataUrl(input.logo) : null,
    footer: footerText(input.footer),
  };
}

function footerText(value: string): string {
  return value.replace(/\r\n/g, "\n").trim();
}

function decodeBase64(value: string): Uint8Array | null {
  try {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  } catch {
    return null;
  }
}

function matchesLogo(mime: LogoMime, bytes: Uint8Array): boolean {
  if (mime === "image/jpeg") {
    return (
      bytes.length >= 3 &&
      bytes[0] === 0xff &&
      bytes[1] === 0xd8 &&
      bytes[2] === 0xff
    );
  }
  if (mime === "image/png") {
    return (
      bytes.length >= 8 &&
      bytes[0] === 0x89 &&
      bytes[1] === 0x50 &&
      bytes[2] === 0x4e &&
      bytes[3] === 0x47 &&
      bytes[4] === 0x0d &&
      bytes[5] === 0x0a &&
      bytes[6] === 0x1a &&
      bytes[7] === 0x0a
    );
  }
  return (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  );
}

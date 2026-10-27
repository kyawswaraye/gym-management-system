import { englishDigitsInput } from "@/lib/ui/english-digits";

const MONEY_INPUT = /^(-?)(0|[1-9]\d{0,15})(?:\.(\d{1,2}))?$/;
const QUANTITY_INPUT = /^(-?)(0|[1-9]\d{0,14})(?:\.(\d{1,3}))?$/;

export type DecimalOptions = {
  allowNegative?: boolean;
  allowZero?: boolean;
};

function canonical(
  value: string,
  pattern: RegExp,
  scale: number,
  options: DecimalOptions,
): string | null {
  const match = pattern.exec(value.trim());
  if (!match?.[2]) return null;
  if (match[1] === "-" && !options.allowNegative) return null;
  const fraction = (match[3] ?? "").padEnd(scale, "0");
  const whole = match[2];
  const negative = match[1] === "-";
  if ([...fraction].every((digit) => digit === "0") && whole === "0") {
    if (!options.allowZero || negative)
      return options.allowZero ? unsignedZero(scale) : null;
    return unsignedZero(scale);
  }
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}

function unsignedZero(scale: number): string {
  return `0.${"0".repeat(scale)}`;
}

export function canonicalMoney(
  value: string,
  options: DecimalOptions = {},
): string | null {
  return canonical(value, MONEY_INPUT, 2, options);
}

/** Keep ASCII digits and one decimal point while a money amount is typed. */
export function englishMoneyInput(value: string): string {
  return englishDigitsInput(value, { kind: "decimal", scale: 2 });
}

export function canonicalQuantity(
  value: string,
  options: DecimalOptions = {},
): string | null {
  return canonical(value, QUANTITY_INPUT, 3, options);
}

export function moneyCents(value: string): bigint | null {
  const canonical = canonicalMoney(value, {
    allowNegative: true,
    allowZero: true,
  });
  if (!canonical) return null;
  const negative = canonical.startsWith("-");
  const digits = canonical.replace("-", "").replace(".", "");
  const cents = BigInt(digits);
  return negative ? -cents : cents;
}

export function formatCents(cents: bigint): string {
  const negative = cents < 0n;
  const absolute = negative ? -cents : cents;
  const digits = absolute.toString().padStart(3, "0");
  const whole = digits.slice(0, -2);
  const fraction = digits.slice(-2);
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}

export function subtractMoney(left: string, right: string): string | null {
  const leftCents = moneyCents(left);
  const rightCents = moneyCents(right);
  if (leftCents === null || rightCents === null) return null;
  return formatCents(leftCents - rightCents);
}

export function previewLineAmount(
  quantity: string,
  unitPrice: string,
  discount: string,
): string | null {
  const canonicalQuantityValue = canonicalQuantity(quantity, {
    allowZero: false,
  });
  const price = moneyCents(unitPrice);
  const discountCents = moneyCents(discount);
  if (!canonicalQuantityValue || price === null || discountCents === null) {
    return null;
  }
  const milli = BigInt(
    canonicalQuantityValue.replace("-", "").replace(".", ""),
  );
  const negative = canonicalQuantityValue.startsWith("-");
  const product = (negative ? -milli : milli) * price;
  const sign = product < 0n ? -1n : 1n;
  const absolute = product < 0n ? -product : product;
  const rounded = sign * ((absolute + 500n) / 1000n);
  return formatCents(rounded - discountCents);
}

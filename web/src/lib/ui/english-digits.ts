export type EnglishDigitsMode =
  | { kind: "integer" }
  | { kind: "code" }
  | { kind: "decimal"; scale: number; allowNegative?: boolean };

export type EnglishDigitsCaret = {
  value: string;
  caret: number;
  selectionEnd: number;
};

const ASCII_DIGIT = /^[0-9]$/;

function isAsciiDigit(char: string): boolean {
  return ASCII_DIGIT.test(char);
}

function normalizeMinus(char: string): string {
  return char === "\u2212" || char === "\uFF0D" ? "-" : char;
}

function stripLeadingZeros(whole: string): string {
  return whole.replace(/^0+(?=\d)/, "");
}

function clampIndex(index: number, length: number): number {
  return Math.max(0, Math.min(index, length));
}

function collapsed(
  value: string,
  caret: number,
): EnglishDigitsCaret {
  const bounded = clampIndex(caret, value.length);
  return { value, caret: bounded, selectionEnd: bounded };
}

type Spot =
  | { kind: "before" }
  | { kind: "sign" }
  | { kind: "whole"; offset: number }
  | { kind: "fraction"; offset: number }
  | { kind: "end" };

function wholeCaret(
  offset: number,
  zerosRemoved: number,
  signLength: number,
): number {
  if (offset <= zerosRemoved) return signLength;
  return signLength + (offset - zerosRemoved);
}

function scanCode(value: string, caret: number): EnglishDigitsCaret {
  let digits = "";
  let nextCaret = 0;
  const bounded = clampIndex(caret, value.length);
  for (let index = 0; index <= value.length; index += 1) {
    if (index === bounded) nextCaret = digits.length;
    if (index === value.length) break;
    const char = value[index] ?? "";
    if (isAsciiDigit(char)) digits += char;
  }
  return collapsed(digits, nextCaret);
}

function scanInteger(value: string, caret: number): EnglishDigitsCaret {
  let whole = "";
  let dropped = false;
  let spot: Spot = { kind: "end" };
  const bounded = clampIndex(caret, value.length);
  for (let index = 0; index <= value.length; index += 1) {
    if (index === bounded) {
      spot = dropped
        ? { kind: "end" }
        : whole.length > 0
          ? { kind: "whole", offset: whole.length }
          : { kind: "before" };
    }
    if (index === value.length) break;
    if (dropped) continue;
    const char = normalizeMinus(value[index] ?? "");
    if (char === ".") {
      dropped = true;
      continue;
    }
    if (isAsciiDigit(char)) whole += char;
  }
  const stripped = stripLeadingZeros(whole);
  const zerosRemoved = whole.length - stripped.length;
  const nextCaret =
    spot.kind === "before"
      ? 0
      : spot.kind === "whole"
        ? wholeCaret(spot.offset, zerosRemoved, 0)
        : stripped.length;
  return collapsed(stripped, nextCaret);
}

function scanDecimal(
  value: string,
  caret: number,
  scale: number,
  allowNegative: boolean,
): EnglishDigitsCaret {
  let negative = false;
  let whole = "";
  let fraction = "";
  let dot = false;
  let spot: Spot = { kind: "end" };
  const bounded = clampIndex(caret, value.length);
  for (let index = 0; index <= value.length; index += 1) {
    if (index === bounded) {
      spot = dot
        ? { kind: "fraction", offset: fraction.length }
        : whole.length > 0
          ? { kind: "whole", offset: whole.length }
          : negative
            ? { kind: "sign" }
            : { kind: "before" };
    }
    if (index === value.length) break;
    const char = normalizeMinus(value[index] ?? "");
    if (char === "-" && allowNegative && !negative && whole === "" && !dot) {
      negative = true;
      continue;
    }
    if (isAsciiDigit(char)) {
      if (dot) {
        if (fraction.length < scale) fraction += char;
      } else {
        whole += char;
      }
      continue;
    }
    if (char === "." && scale > 0 && !dot) dot = true;
  }

  const stripped = stripLeadingZeros(whole);
  const zerosRemoved = whole.length - stripped.length;
  const insertedZero = stripped === "" && (dot || fraction !== "");
  const finalWhole = insertedZero ? "0" : stripped;
  const sign = negative ? "-" : "";
  if (!finalWhole && !dot) {
    return collapsed(sign, spot.kind === "before" ? 0 : sign.length);
  }
  const text = dot ? `${sign}${finalWhole}.${fraction}` : `${sign}${finalWhole}`;
  if (
    insertedZero &&
    ((spot.kind === "before" && !negative) || spot.kind === "sign")
  ) {
    const start = sign.length;
    return {
      value: text,
      caret: start,
      selectionEnd: Math.min(start + 1, text.length),
    };
  }
  const nextCaret =
    spot.kind === "before"
      ? 0
      : spot.kind === "sign"
        ? sign.length
        : spot.kind === "whole"
          ? wholeCaret(spot.offset, zerosRemoved, sign.length)
          : spot.kind === "fraction"
            ? sign.length +
              finalWhole.length +
              1 +
              Math.min(spot.offset, fraction.length)
            : text.length;
  return collapsed(text, nextCaret);
}

/** Keep English digits. Decimals and a leading minus are allowed only by mode. */
export function englishDigitsInput(
  value: string,
  mode: EnglishDigitsMode,
): string {
  return englishDigitsCaret(value, value.length, mode).value;
}

export function englishDigitsCaret(
  value: string,
  caret: number,
  mode: EnglishDigitsMode,
): EnglishDigitsCaret {
  if (mode.kind === "code") return scanCode(value, caret);
  if (mode.kind === "integer") return scanInteger(value, caret);
  return scanDecimal(value, caret, mode.scale, mode.allowNegative === true);
}

function limitMapped(
  mapped: EnglishDigitsCaret,
  maxLength: number,
): EnglishDigitsCaret {
  if (maxLength > 0 && mapped.value.length > maxLength) {
    const value = mapped.value.slice(0, maxLength);
    const caret = Math.min(mapped.caret, value.length);
    const selectionEnd = Math.max(
      caret,
      Math.min(mapped.selectionEnd, value.length),
    );
    return { value, caret, selectionEnd };
  }
  return mapped;
}

function placeCaret(
  input: HTMLInputElement,
  caret: number,
  selectionEnd: number,
): void {
  try {
    input.setSelectionRange(caret, selectionEnd);
  } catch {
    // Number inputs reject selection changes. Digit fields use text.
  }
}

/** Rewrite the field in place and return the caret when the text changed. */
export function applyEnglishDigits(
  input: HTMLInputElement,
  mode: EnglishDigitsMode,
): { start: number; end: number } | null {
  const mapped = limitMapped(
    englishDigitsCaret(
      input.value,
      input.selectionStart ?? input.value.length,
      mode,
    ),
    input.maxLength,
  );
  if (mapped.value === input.value) return null;
  input.value = mapped.value;
  placeCaret(input, mapped.caret, mapped.selectionEnd);
  return { start: mapped.caret, end: mapped.selectionEnd };
}

export function pasteEnglishDigits(
  input: HTMLInputElement,
  text: string,
  mode: EnglishDigitsMode,
): { start: number; end: number } {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  const merged = input.value.slice(0, start) + text + input.value.slice(end);
  const mapped = limitMapped(
    englishDigitsCaret(merged, start + text.length, mode),
    input.maxLength,
  );
  input.value = mapped.value;
  placeCaret(input, mapped.caret, mapped.selectionEnd);
  return { start: mapped.caret, end: mapped.selectionEnd };
}

function selectionRemainder(input: HTMLInputElement): {
  start: number;
  end: number;
  rest: string;
} {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  return {
    start,
    end,
    rest: input.value.slice(0, start) + input.value.slice(end),
  };
}

/** Block one disallowed keystroke. Pastes are cleaned after they land. */
export function blockDisallowedEnglishDigit(
  event: {
    data: string | null;
    defaultPrevented: boolean;
    preventDefault(): void;
    currentTarget: HTMLInputElement;
  },
  mode: EnglishDigitsMode,
): void {
  if (event.defaultPrevented) return;
  const data = event.data;
  if (data == null || data.length !== 1) return;
  const input = event.currentTarget;
  const { start, end, rest } = selectionRemainder(input);
  const char = normalizeMinus(data);

  if (isAsciiDigit(char)) {
    if (mode.kind !== "decimal" || start !== end) return;
    const dot = input.value.indexOf(".");
    if (dot < 0 || start <= dot) return;
    const fractionLength = input.value.length - dot - 1;
    if (fractionLength >= mode.scale) event.preventDefault();
    return;
  }

  if (
    char === "." &&
    mode.kind === "decimal" &&
    mode.scale > 0 &&
    !rest.includes(".")
  ) {
    return;
  }
  if (
    char === "-" &&
    mode.kind === "decimal" &&
    mode.allowNegative &&
    start === 0 &&
    !rest.includes("-")
  ) {
    return;
  }
  event.preventDefault();
}

export function englishDigitsModeFromInput(options: {
  type?: string | undefined;
  inputMode?: string | undefined;
  step?: number | string | undefined;
  decimalPlaces?: number | undefined;
  allowNegative?: boolean | undefined;
  keepLeadingZeros?: boolean | undefined;
}): EnglishDigitsMode | null {
  if (options.keepLeadingZeros) return { kind: "code" };
  if (options.decimalPlaces != null) {
    if (options.decimalPlaces <= 0) return { kind: "integer" };
    return {
      kind: "decimal",
      scale: options.decimalPlaces,
      ...(options.allowNegative ? { allowNegative: true } : {}),
    };
  }
  if (options.inputMode === "decimal" || options.allowNegative) {
    return {
      kind: "decimal",
      scale: 2,
      ...(options.allowNegative ? { allowNegative: true } : {}),
    };
  }
  if (options.inputMode === "numeric") return { kind: "integer" };
  if (options.type === "number") {
    const step = options.step == null ? "" : String(options.step);
    const dot = step.indexOf(".");
    const scale = dot < 0 ? 0 : step.length - dot - 1;
    if (scale > 0) return { kind: "decimal", scale };
    return { kind: "integer" };
  }
  return null;
}

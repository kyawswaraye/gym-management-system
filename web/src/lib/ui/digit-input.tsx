"use client";

import {
  useLayoutEffect,
  useRef,
  type ChangeEvent,
  type InputHTMLAttributes,
} from "react";

import {
  applyEnglishDigits,
  blockDisallowedEnglishDigit,
  pasteEnglishDigits,
  type EnglishDigitsMode,
} from "@/lib/ui/english-digits";

type Caret = { start: number; end: number };

function atLeastOne(min: number | string | undefined): boolean {
  if (min == null || min === "") return false;
  const value = typeof min === "number" ? min : Number(min);
  return Number.isFinite(value) && value >= 1;
}

function changeEvent(input: HTMLInputElement): ChangeEvent<HTMLInputElement> {
  return {
    currentTarget: input,
    target: input,
  } as ChangeEvent<HTMLInputElement>;
}

export function DigitInput({
  mode,
  onChange,
  onBeforeInput,
  onPaste,
  lang,
  pattern,
  min,
  value: valueProp,
  type: _type,
  inputMode: _inputMode,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { mode: EnglishDigitsMode }) {
  const ref = useRef<HTMLInputElement>(null);
  const caretRef = useRef<Caret | null>(null);
  const controlled = valueProp !== undefined;
  const resolvedPattern =
    pattern ??
    (mode.kind === "integer" && atLeastOne(min) ? "[1-9]\\d*" : undefined);

  useLayoutEffect(() => {
    const input = ref.current;
    const caret = caretRef.current;
    if (!input || caret == null) return;
    caretRef.current = null;
    if (document.activeElement !== input) return;
    const start = Math.max(0, Math.min(caret.start, input.value.length));
    const end = Math.max(start, Math.min(caret.end, input.value.length));
    try {
      input.setSelectionRange(start, end);
    } catch {
      // Selection is unavailable until the field can accept text.
    }
  });

  return (
    <input
      {...props}
      ref={ref}
      autoCapitalize="off"
      autoCorrect="off"
      inputMode={mode.kind === "decimal" ? "decimal" : "numeric"}
      lang={lang ?? "en"}
      {...(min != null ? { min } : {})}
      {...(resolvedPattern ? { pattern: resolvedPattern } : {})}
      spellCheck={false}
      type="text"
      {...(controlled ? { value: valueProp } : {})}
      onBeforeInput={(event) => {
        const input = event.currentTarget;
        if (input instanceof HTMLInputElement) {
          blockDisallowedEnglishDigit(
            {
              data: event.data ?? null,
              defaultPrevented: event.defaultPrevented,
              preventDefault: () => event.preventDefault(),
              currentTarget: input,
            },
            mode,
          );
        }
        onBeforeInput?.(event);
      }}
      onPaste={(event) => {
        const text = event.clipboardData?.getData("text");
        if (text == null) {
          onPaste?.(event);
          return;
        }
        event.preventDefault();
        const input = event.currentTarget;
        const previous = input.value;
        const caret = pasteEnglishDigits(input, text, mode);
        if (input.value !== previous) {
          if (controlled) caretRef.current = caret;
          onChange?.(changeEvent(input));
        }
        onPaste?.(event);
      }}
      onChange={(event) => {
        const caret = applyEnglishDigits(event.currentTarget, mode);
        if (caret && controlled) caretRef.current = caret;
        onChange?.(event);
      }}
    />
  );
}

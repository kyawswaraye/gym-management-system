import { describe, expect, it } from "vitest";

import {
  applyEnglishDigits,
  blockDisallowedEnglishDigit,
  englishDigitsCaret,
  englishDigitsInput,
  pasteEnglishDigits,
  type EnglishDigitsMode,
} from "@/lib/ui/english-digits";

const MYANMAR = "၀၁၂၃၄၅၆၇၈၉";

describe("english digit input", () => {
  it("keeps money in ASCII digits with one decimal and two places", () => {
    const money = { kind: "decimal" as const, scale: 2 };
    expect(englishDigitsInput("20၀0a.5x99", money)).toBe("200.59");
    expect(englishDigitsInput("၂၀၀၀၀၀", money)).toBe("");
    expect(englishDigitsInput(MYANMAR, money)).toBe("");
    expect(englishDigitsInput("٠١٢", money)).toBe("");
    expect(englishDigitsInput("０１２", money)).toBe("");
    expect(englishDigitsInput("020", money)).toBe("20");
    expect(englishDigitsInput(".5", money)).toBe("0.5");
    expect(englishDigitsInput("10.", money)).toBe("10.");
    expect(englishDigitsInput("10.50", money)).toBe("10.50");
    expect(englishDigitsInput("10.5.5", money)).toBe("10.55");
    expect(englishDigitsInput("-10.50", money)).toBe("10.50");
    expect(englishDigitsInput("1,000.50", money)).toBe("1000.50");
    expect(englishDigitsInput(" 2000.5 ", money)).toBe("2000.5");
  });

  it("keeps three decimal places for quantities and a leading minus only when signed", () => {
    const quantity = { kind: "decimal" as const, scale: 3 };
    const signed = { ...quantity, allowNegative: true };
    expect(englishDigitsInput("1.2345", quantity)).toBe("1.234");
    expect(englishDigitsInput("-1.5", quantity)).toBe("1.5");
    expect(englishDigitsInput("-1.5", signed)).toBe("-1.5");
    expect(englishDigitsInput("\u22121.5", signed)).toBe("-1.5");
    expect(englishDigitsInput("-", signed)).toBe("-");
    expect(englishDigitsInput("-.", signed)).toBe("-0.");
    expect(englishDigitsInput("1-2", signed)).toBe("12");
    expect(englishDigitsInput("--5", signed)).toBe("-5");
    expect(englishDigitsInput("0.000", quantity)).toBe("0.000");
  });

  it("keeps whole numbers and stops at a decimal point", () => {
    const integer = { kind: "integer" as const };
    expect(englishDigitsInput("12a.99", integer)).toBe("12");
    expect(englishDigitsInput(" 12 ", integer)).toBe("12");
    expect(englishDigitsInput("၀12", integer)).toBe("12");
    expect(englishDigitsInput("01", integer)).toBe("1");
    expect(englishDigitsInput("0", integer)).toBe("0");
    expect(englishDigitsInput("100", integer)).toBe("100");
    expect(englishDigitsInput("-4", integer)).toBe("4");
  });

  it("keeps leading zeros in digit codes and drops every other character", () => {
    const code = { kind: "code" as const };
    expect(englishDigitsInput("012345", code)).toBe("012345");
    expect(englishDigitsInput("12 3-456", code)).toBe("123456");
    expect(englishDigitsInput("12a.3", code)).toBe("123");
    expect(englishDigitsInput("၀12", code)).toBe("12");
  });

  it("places the caret on the cleaned text", () => {
    const money = { kind: "decimal" as const, scale: 2 };
    expect(englishDigitsCaret("1a.00", 2, money)).toEqual({
      value: "1.00",
      caret: 1,
      selectionEnd: 1,
    });
    expect(englishDigitsCaret(".5", 2, money)).toEqual({
      value: "0.5",
      caret: 3,
      selectionEnd: 3,
    });
    expect(englishDigitsCaret("12.99", 5, { kind: "integer" })).toEqual({
      value: "12",
      caret: 2,
      selectionEnd: 2,
    });
    expect(englishDigitsCaret("010", 1, money)).toEqual({
      value: "10",
      caret: 0,
      selectionEnd: 0,
    });
    expect(englishDigitsCaret(".5", 0, money)).toEqual({
      value: "0.5",
      caret: 0,
      selectionEnd: 1,
    });
    expect(englishDigitsCaret("-.5", 1, signedQuantity)).toEqual({
      value: "-0.5",
      caret: 1,
      selectionEnd: 2,
    });
  });

  it("rewrites a field without dropping a valid decimal while editing", () => {
    const input = document.createElement("input");
    input.value = "10.50";
    input.setSelectionRange(5, 5);
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toBeNull();
    expect(input.value).toBe("10.50");

    input.value = "10.5";
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toBeNull();
    input.value = "10.";
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toBeNull();
    input.value = "";
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toBeNull();

    input.value = "20၀0a.5x99";
    input.setSelectionRange(input.value.length, input.value.length);
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toEqual({
      start: 6,
      end: 6,
    });
    expect(input.value).toBe("200.59");
    expect(input.selectionStart).toBe(6);

    input.value = ".5";
    input.setSelectionRange(0, 0);
    expect(applyEnglishDigits(input, { kind: "decimal", scale: 2 })).toEqual({
      start: 0,
      end: 1,
    });
    expect(input.value).toBe("0.5");
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(1);
  });

  it("pastes only the English digits and respects a length limit", () => {
    const input = document.createElement("input");
    input.maxLength = 6;
    input.value = "12";
    input.setSelectionRange(2, 2);
    expect(pasteEnglishDigits(input, "3 4-56၇8", { kind: "code" })).toBe(6);
    expect(input.value).toBe("123456");
  });
});

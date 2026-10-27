import { describe, expect, it } from "vitest";

import {
  canonicalMoney,
  canonicalQuantity,
  englishMoneyInput,
  previewLineAmount,
  subtractMoney,
} from "@/features/finance/money";

describe("exact finance decimals", () => {
  it("keeps money at two decimals and quantities at three", () => {
    expect(canonicalMoney("10")).toBe("10.00");
    expect(canonicalMoney("10.5")).toBe("10.50");
    expect(canonicalMoney("10.555")).toBeNull();
    expect(canonicalMoney("0.00")).toBeNull();
    expect(canonicalMoney("0.00", { allowZero: true })).toBe("0.00");
    expect(canonicalQuantity("1.25")).toBe("1.250");
    expect(canonicalQuantity("1.2500")).toBeNull();
    expect(canonicalQuantity("-2", { allowNegative: true })).toBe("-2.000");
    expect(canonicalQuantity("0.000")).toBeNull();
  });

  it("keeps typed money in English digits with at most two decimal places", () => {
    expect(englishMoneyInput("20၀0a.5x99")).toBe("200.59");
    expect(englishMoneyInput("၂၀၀၀၀၀")).toBe("");
    expect(englishMoneyInput("020")).toBe("20");
    expect(englishMoneyInput(".5")).toBe("0.5");
    expect(englishMoneyInput("10.5.5")).toBe("10.55");
  });

  it("previews a half-up line without treating it as a posted total", () => {
    expect(previewLineAmount("1.250", "0.10", "0.00")).toBe("0.13");
    expect(subtractMoney("90.00", "100.00")).toBe("-10.00");
  });
});

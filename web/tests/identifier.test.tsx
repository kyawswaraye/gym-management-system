import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { compactIdentifier, IdentifierValue } from "@/lib/ui/identifier";

const UUID = "16a40028-38bb-4b79-8cd7-c87c9aa8a63f";

describe("compactIdentifier", () => {
  it("keeps the last six characters of a uuid", () => {
    expect(compactIdentifier(UUID)).toBe("a8a63f");
    expect(compactIdentifier(`  ${UUID.toUpperCase()}  `)).toBe("A8A63F");
    expect(compactIdentifier("ab12cd34ef56ab12cd34ef56ab12cd34")).toBe(
      "12cd34",
    );
  });

  it("leaves ordinary text unchanged", () => {
    for (const value of [
      "Ada Lovelace",
      "M-0008",
      "MONTH",
      "SET-A",
      "req_test",
      "None",
      "missing",
      "1399.50 THB",
      "2026-10-02",
      "2026-10-02T00:00:00Z",
      "AB12CD34EF56GH78IJ90KL12MN34OP56",
      "Paid in full for the annual plan",
    ]) {
      expect(compactIdentifier(value)).toBe(value);
    }
  });
});

describe("IdentifierValue", () => {
  it("shows the short id and keeps the full value available", () => {
    render(<IdentifierValue value={UUID} />);

    const shown = screen.getByTitle(UUID);
    expect(shown).toHaveTextContent("a8a63f");
    expect(shown).toHaveAccessibleName(UUID);
    expect(screen.queryByText(UUID)).not.toBeInTheDocument();
  });

  it("renders human-readable text without a tooltip", () => {
    render(<IdentifierValue value="Ada Lovelace" />);

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.queryByTitle("Ada Lovelace")).not.toBeInTheDocument();
  });
});

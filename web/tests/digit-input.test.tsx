import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { TextInput } from "@/features/finance/ui";
import { DigitInput } from "@/lib/ui/digit-input";

function MoneyField() {
  const [value, setValue] = useState("10.00");
  return (
    <TextInput
      aria-label="Amount"
      inputMode="decimal"
      onChange={(event) => setValue(event.target.value)}
      value={value}
    />
  );
}

describe("digit inputs", () => {
  it("leaves names and dates unrestricted", () => {
    render(
      <>
        <TextInput aria-label="Name" />
        <TextInput aria-label="When" type="date" />
      </>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Ada ၀" },
    });
    fireEvent.change(screen.getByLabelText("When"), {
      target: { value: "2026-10-28" },
    });
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Ada ၀");
    expect(screen.getByLabelText("When")).toHaveValue("2026-10-28");
  });

  it("keeps a controlled money amount in English digits while it is edited", () => {
    render(<MoneyField />);
    const amount = screen.getByRole("textbox", { name: "Amount" });
    expect(amount).toHaveValue("10.00");

    fireEvent.change(amount, { target: { value: "10.5" } });
    expect(amount).toHaveValue("10.5");
    fireEvent.change(amount, { target: { value: "10." } });
    expect(amount).toHaveValue("10.");
    fireEvent.change(amount, { target: { value: "" } });
    expect(amount).toHaveValue("");
    fireEvent.change(amount, { target: { value: ".5" } });
    expect(amount).toHaveValue("0.5");
    fireEvent.change(amount, { target: { value: "20၀0a.5x99" } });
    expect(amount).toHaveValue("200.59");
  });

  it("drops letters from whole-number fields and keeps quantity scale", () => {
    render(
      <>
        <TextInput aria-label="Sessions" step={1} type="number" />
        <TextInput
          aria-label="Quantity"
          decimalPlaces={3}
          inputMode="decimal"
        />
        <TextInput
          allowNegative
          aria-label="Adjustment"
          decimalPlaces={3}
          inputMode="decimal"
        />
        <DigitInput aria-label="Code" maxLength={6} mode={{ kind: "code" }} />
      </>,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Sessions" }), {
      target: { value: "1a2.99" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Quantity" }), {
      target: { value: "1.2345x" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Adjustment" }), {
      target: { value: "-1.25a" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Code" }), {
      target: { value: "0၁2 3456" },
    });

    expect(screen.getByRole("textbox", { name: "Sessions" })).toHaveValue("12");
    expect(screen.getByRole("textbox", { name: "Quantity" })).toHaveValue(
      "1.234",
    );
    expect(screen.getByRole("textbox", { name: "Adjustment" })).toHaveValue(
      "-1.25",
    );
    expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue("023456");
  });
});

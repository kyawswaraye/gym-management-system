import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { LocaleProvider } from "@/i18n/use-tx";
import { MemberProfileFields } from "@/features/operations/member-profile-fields";
import {
  emptyMemberProfile,
  type MemberProfileInput,
} from "@/features/operations/member-profile";

function Harness({ locale = "en" }: { locale?: "en" | "mm" }) {
  const [value, setValue] = useState<MemberProfileInput>(() =>
    emptyMemberProfile("2026-01-01"),
  );
  return (
    <LocaleProvider locale={locale}>
      <MemberProfileFields
        disabled={false}
        includeMemberNumber={false}
        onChange={setValue}
        value={value}
      />
    </LocaleProvider>
  );
}

describe("Myanmar address choices", () => {
  it("lists regions and limits cities to the chosen region", () => {
    render(<Harness />);
    const region = screen.getByLabelText("Region");
    const city = screen.getByLabelText("City");

    expect(city).toBeDisabled();
    expect(screen.getByText("Choose a region first.")).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Yangon Region" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Kamayut" }),
    ).not.toBeInTheDocument();

    fireEvent.change(region, { target: { value: "Yangon Region" } });

    expect(city).toBeEnabled();
    expect(city).toHaveValue("");
    expect(screen.getByRole("option", { name: "Kamayut" })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Mandalay" }),
    ).not.toBeInTheDocument();

    fireEvent.change(city, { target: { value: "Kamayut" } });
    expect(city).toHaveValue("Kamayut");
  });

  it("shows Myanmar names when the language is Myanmar", () => {
    render(<Harness locale="mm" />);
    expect(
      screen.getByRole("option", { name: "ရန်ကုန်တိုင်းဒေသကြီး" }),
    ).toBeInTheDocument();
    expect(screen.getByText("တိုင်းဒေသကို အရင်ရွေးပါ။")).toBeInTheDocument();
  });
});

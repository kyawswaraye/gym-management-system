import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { LanguageSwitch } from "@/i18n/language-switch";
import { messages } from "@/i18n/messages";
import { LocaleProvider } from "@/i18n/use-tx";
import type { CurrentUser } from "@/types/api";

const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ refresh, replace: vi.fn() }),
}));

function session(): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions: ["gym.view"],
    features: [],
    mfa_enabled: true,
    user: { first_name: "Ada", email: "ada@example.test" },
    gym: { name: "Yangon Fitness" },
  } as unknown as CurrentUser;
}

describe("locale catalogs", () => {
  it("gives Myanmar every English sentence", () => {
    expect(Object.keys(messages.mm).sort()).toEqual(
      Object.keys(messages.en).sort(),
    );
  });
});

describe("sidebar", () => {
  it("shows Myanmar labels while the gym name stays unchanged", () => {
    render(
      <LocaleProvider locale="mm">
        <AppShell session={session()}>
          <p>Workspace</p>
        </AppShell>
      </LocaleProvider>,
    );

    expect(screen.getByRole("link", { name: "ဆက်တင်" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getByText("Yangon Fitness")).toBeInTheDocument();
    const footer = screen.getByRole("contentinfo");
    expect(within(footer).queryByRole("button", { name: "MM" })).toBeNull();
    fireEvent.click(within(footer).getByRole("button", { name: /Ada/ }));
    expect(screen.getByRole("button", { name: "MM" })).toBeVisible();
    expect(screen.getByText("ဘာသာစကား")).toBeVisible();
  });
});

describe("language switch", () => {
  it("stores the Myanmar choice and refreshes the page", () => {
    render(
      <LocaleProvider locale="en">
        <LanguageSwitch />
      </LocaleProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "MM" }));

    expect(document.cookie).toContain("gp_locale=mm");
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});

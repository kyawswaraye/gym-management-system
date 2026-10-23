import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { NavLink, navLinkIsCurrent } from "@/components/layout/nav-link";
import type { CurrentUser } from "@/types/api";

const pathname = vi.hoisted(() => ({ value: "/members" }));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname.value,
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

const LINKS = [
  ["Dashboard", "/dashboard"],
  ["Members", "/members"],
  ["Memberships", "/memberships"],
  ["Check-ins", "/check-ins"],
  ["PT sales", "/pt/sales"],
  ["Packages", "/pt/packages"],
] as const;

const HREFS = LINKS.map(([, href]) => href);

function renderLinks() {
  return render(
    <nav aria-label="Primary">
      {LINKS.map(([label, href]) => (
        <NavLink href={href} key={href} peers={HREFS}>
          {label}
        </NavLink>
      ))}
    </nav>,
  );
}

function expectCurrent(name: string) {
  const current = screen.getByRole("link", { name });
  expect(current).toHaveAttribute("aria-current", "page");
  expect(current.className).toContain("bg-amber-400/20");
  expect(current.className).toContain("shadow-[inset_3px_0_0_#fbbf24]");
  return current;
}

function expectIdle(name: string) {
  const link = screen.getByRole("link", { name });
  expect(link).not.toHaveAttribute("aria-current");
  expect(link.className).toContain("hover:bg-white/10");
  expect(link.className).toContain("focus-visible:outline-amber-300");
}

function session(
  permissions: string[],
  features: string[],
  platform = false,
): CurrentUser {
  return {
    principal_type: platform ? "PLATFORM" : "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    mfa_enabled: true,
    user: { first_name: "Ada", email: "ada@example.test" },
    gym: { name: "Yangon Fitness" },
    gym_membership: { id: "membership-1", is_owner: false, roles: [] },
  } as unknown as CurrentUser;
}

describe("navigation link active state", () => {
  it("matches an exact route and a nested page without a false prefix", () => {
    expect(navLinkIsCurrent("/members", "/members", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/members/", "/members", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/members/member-1", "/members", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/members?status=ACTIVE", "/members", HREFS)).toBe(
      true,
    );
    expect(navLinkIsCurrent("/memberships", "/members", HREFS)).toBe(false);
    expect(navLinkIsCurrent("/pt/sales", "/pt/sales", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/pt/sales/sale-1", "/pt/sales", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/pt/salesperson", "/pt/sales", HREFS)).toBe(false);
    expect(navLinkIsCurrent("/pt/salesroom", "/pt/sales", HREFS)).toBe(false);
    expect(navLinkIsCurrent("/check-ins", "/check-ins", HREFS)).toBe(true);
    expect(navLinkIsCurrent("/check-ins?view=qr", "/check-ins", HREFS)).toBe(
      true,
    );
    expect(navLinkIsCurrent("/check-ins/display", "/check-ins", HREFS)).toBe(
      true,
    );
    expect(
      navLinkIsCurrent("/check-ins/display", "/check-ins", [
        "/check-ins",
        "/check-ins/display",
      ]),
    ).toBe(false);
    expect(
      navLinkIsCurrent("/check-ins/display", "/check-ins/display", [
        "/check-ins",
        "/check-ins/display",
      ]),
    ).toBe(true);
    expect(navLinkIsCurrent("/dashboard", "/pt/dashboard", HREFS)).toBe(false);
  });

  it("marks the current link and leaves unrelated links idle", () => {
    pathname.value = "/members";
    const view = renderLinks();
    expectCurrent("Members");
    expectIdle("Memberships");
    expectIdle("PT sales");
    expect(screen.getByRole("link", { name: "Members" })).toHaveAttribute(
      "href",
      "/members",
    );

    pathname.value = "/members/member-1";
    view.rerender(
      <nav aria-label="Primary">
        {LINKS.map(([label, href]) => (
          <NavLink href={href} key={href} peers={HREFS}>
            {label}
          </NavLink>
        ))}
      </nav>,
    );
    expectCurrent("Members");
    expectIdle("Memberships");
    expectIdle("Dashboard");

    pathname.value = "/pt/salesperson";
    view.rerender(
      <nav aria-label="Primary">
        {LINKS.map(([label, href]) => (
          <NavLink href={href} key={href} peers={HREFS}>
            {label}
          </NavLink>
        ))}
      </nav>,
    );
    expectIdle("PT sales");
    expectIdle("Packages");
    expectIdle("Members");

    pathname.value = "/check-ins/display";
    view.rerender(
      <nav aria-label="Primary">
        {LINKS.map(([label, href]) => (
          <NavLink href={href} key={href} peers={HREFS}>
            {label}
          </NavLink>
        ))}
      </nav>,
    );
    expectCurrent("Check-ins");
    expectIdle("Members");

    pathname.value = "/check-ins?view=qr";
    view.rerender(
      <nav aria-label="Primary">
        {LINKS.map(([label, href]) => (
          <NavLink href={href} key={href} peers={HREFS}>
            {label}
          </NavLink>
        ))}
      </nav>,
    );
    expectCurrent("Check-ins");
    expectIdle("Memberships");
  });

  it("marks the sidebar page in the tenant and platform shells", () => {
    pathname.value = "/members/member-1";
    const tenant = render(
      <AppShell
        session={session(
          [
            "members.view",
            "memberships.view",
            "checkins.view",
            "checkins.challenge.issue",
            "pt.sales.view",
          ],
          ["members", "memberships", "pt"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expectCurrent("Members");
    expectIdle("Memberships");
    expectIdle("PT sales");
    expectIdle("Check-ins");
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).not.toHaveAttribute("aria-current");
    tenant.unmount();

    pathname.value = "/pt/sales";
    const sales = render(
      <AppShell
        session={session(
          ["members.view", "pt.sales.view", "pt.schedule.view"],
          ["members", "pt"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expectCurrent("PT sales");
    expectIdle("Members");
    expectIdle("Packages");
    sales.unmount();

    pathname.value = "/check-ins/display";
    const display = render(
      <AppShell
        session={session(
          ["checkins.view", "checkins.challenge.issue"],
          ["members", "memberships"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expectCurrent("Check-ins");
    expect(screen.getAllByRole("link", { name: "Check-ins" })).toHaveLength(1);
    expect(
      screen.queryByRole("link", { name: "Check-in QR" }),
    ).not.toBeInTheDocument();
    display.unmount();

    pathname.value = "/check-ins?view=qr";
    const qrView = render(
      <AppShell
        session={session(
          ["checkins.view", "checkins.challenge.issue"],
          ["members", "memberships"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expectCurrent("Check-ins");
    expect(screen.getByRole("link", { name: "Check-ins" })).toHaveAttribute(
      "href",
      "/check-ins",
    );
    qrView.unmount();

    pathname.value = "/super-admin/gyms/gym-1";
    render(
      <AppShell
        platform
        session={session(
          ["platform.dashboard.view", "platform.gyms.view"],
          [],
          true,
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expectCurrent("Gyms");
    expectIdle("Dashboard");
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).not.toHaveAttribute("aria-current");
  });

  it("shows one Check-ins link when either desk or QR access is available", () => {
    pathname.value = "/members";
    const desk = render(
      <AppShell
        session={session(["checkins.view"], ["members", "memberships"])}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Check-ins" })).toHaveAttribute(
      "href",
      "/check-ins",
    );
    expect(
      screen.queryByRole("link", { name: "Check-in QR" }),
    ).not.toBeInTheDocument();
    desk.unmount();

    const qr = render(
      <AppShell
        session={session(
          ["checkins.challenge.issue"],
          ["members", "memberships"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Check-ins" })).toHaveAttribute(
      "href",
      "/check-ins",
    );
    expect(
      screen.queryByRole("link", { name: "Check-in QR" }),
    ).not.toBeInTheDocument();
    qr.unmount();

    const both = render(
      <AppShell
        session={session(
          ["checkins.view", "checkins.challenge.issue"],
          ["members", "memberships"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getAllByRole("link", { name: "Check-ins" })).toHaveLength(1);
    both.unmount();

    const missingFeature = render(
      <AppShell
        session={session(
          ["checkins.view", "checkins.challenge.issue"],
          ["members"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Check-ins" }),
    ).not.toBeInTheDocument();
    missingFeature.unmount();

    render(
      <AppShell session={session(["members.view"], ["members"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Check-ins" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Check-in QR" }),
    ).not.toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import type { CurrentUser } from "@/types/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

function session(
  permissions: string[],
  features: string[] = ["pt"],
  roles: string[] = [],
): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: { name: "Yangon Fitness" },
    gym_membership: {
      id: "membership-1",
      is_owner: roles.includes("OWNER"),
      roles,
    },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as unknown as CurrentUser;
}

const LINKS = [
  ["Trainers", "/pt/trainers"],
  ["Packages", "/pt/packages"],
  ["Member packages", "/pt/member-packages"],
  ["PT sales", "/pt/sales"],
  ["Commissions", "/pt/commissions"],
  ["PT bookings", "/pt/bookings"],
  ["Classes", "/classes"],
] as const;

describe("phase 5 navigation", () => {
  it("shows every published page for an owner with the pt entitlement", () => {
    render(
      <AppShell
        session={session(
          [
            "pt.assignments.view",
            "pt.schedule.view",
            "pt.sales.view",
            "pt.commissions.view",
            "classes.view",
          ],
          ["pt"],
          ["OWNER"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    for (const [name, href] of LINKS) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    }
  });

  it("hides each route when the entitlement or view permission is missing", () => {
    const { rerender } = render(
      <AppShell
        session={session(
          [
            "pt.assignments.view",
            "pt.schedule.view",
            "pt.sales.view",
            "pt.commissions.view",
            "classes.view",
          ],
          [],
          ["MANAGER"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    for (const [name] of LINKS) {
      expect(screen.queryByRole("link", { name })).not.toBeInTheDocument();
    }

    rerender(
      <AppShell session={session(["pt.assignments.view"], ["pt"], ["TRAINER"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Trainers" })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Packages" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "PT sales" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Commissions" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Classes" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell
        session={session(["pt.commissions.view"], ["pt"], ["ACCOUNTANT"])}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.getByRole("link", { name: "Commissions" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Trainers" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell session={session(["classes.view"], ["pt"], ["RECEPTIONIST"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Classes" })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "PT bookings" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell session={session(["pt.schedule.view"], ["pt"], ["DESK"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Packages" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Member packages" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "PT bookings" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Trainers" }),
    ).not.toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import type { CurrentUser } from "@/types/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

function session(
  permissions: string[],
  platform = false,
  features: Array<{ feature__code: string }> = [],
): CurrentUser {
  return {
    principal_type: platform ? "PLATFORM" : "STAFF",
    permissions,
    features,
    mfa_enabled: true,
    user: { first_name: "Ada", email: "ada@example.test" },
    gym: { name: "Yangon Fitness" },
  } as unknown as CurrentUser;
}

describe("tenant settings navigation", () => {
  it("links readers to gym settings and hides the link otherwise", () => {
    const { rerender } = render(
      <AppShell session={session(["gym.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );

    rerender(
      <AppShell session={session(["members.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Settings" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell platform session={session(["gym.view"], true)}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Settings" }),
    ).not.toBeInTheDocument();
  });

  it("links staff readers and hides the link without staff.view", () => {
    const { rerender } = render(
      <AppShell session={session(["staff.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Staff" })).toHaveAttribute(
      "href",
      "/staff",
    );

    rerender(
      <AppShell session={session(["staff.manage", "roles.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Staff" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell platform session={session(["staff.view"], true)}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Staff" }),
    ).not.toBeInTheDocument();
  });

  it("links role readers and hides the link without roles.view", () => {
    const { rerender } = render(
      <AppShell session={session(["roles.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Roles" })).toHaveAttribute(
      "href",
      "/roles",
    );

    rerender(
      <AppShell session={session(["roles.manage", "staff.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Roles" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell platform session={session(["roles.view"], true)}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Roles" }),
    ).not.toBeInTheDocument();
  });

  it("shows platform links and homes only for matching permissions", () => {
    const { rerender } = render(
      <AppShell
        platform
        session={session(["platform.subscriptions.manage"], true)}
      >
        <p>Workspace</p>
      </AppShell>,
    );

    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/super-admin/subscriptions");
    expect(screen.getByRole("link", { name: "Subscriptions" })).toHaveAttribute(
      "href",
      "/super-admin/subscriptions",
    );
    expect(
      screen.queryByRole("link", { name: "Dashboard" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Gyms" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Audit logs" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell
        platform
        session={session(
          ["platform.dashboard.view", "platform.audit.view"],
          true,
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/super-admin/dashboard",
    );
    expect(screen.getByRole("link", { name: "Audit logs" })).toHaveAttribute(
      "href",
      "/super-admin/audit-logs",
    );
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/super-admin/dashboard");

    rerender(
      <AppShell platform session={session(["platform.settings.manage"], true)}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/super-admin");
    expect(
      screen.queryByRole("link", { name: "Dashboard" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Subscriptions" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Audit logs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Gyms" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Settings" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell session={session(["platform.audit.view", "members.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Super admin" })).toHaveAttribute(
      "href",
      "/super-admin/audit-logs",
    );
    expect(
      screen.queryByRole("link", { name: "Audit logs" }),
    ).not.toBeInTheDocument();
  });

  it("links tenant audit readers to tenant audit logs", () => {
    const { rerender } = render(
      <AppShell session={session(["audit.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Audit logs" })).toHaveAttribute(
      "href",
      "/audit-logs",
    );
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/audit-logs");

    rerender(
      <AppShell session={session(["staff.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Audit logs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/staff");

    rerender(
      <AppShell
        platform
        session={session(["audit.view", "platform.audit.view"], true)}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getAllByRole("link", { name: "Audit logs" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Audit logs" })).toHaveAttribute(
      "href",
      "/super-admin/audit-logs",
    );

    rerender(
      <AppShell session={session(["audit.view", "platform.audit.view"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Audit logs" })).toHaveAttribute(
      "href",
      "/audit-logs",
    );
    expect(screen.getByRole("link", { name: "Super admin" })).toHaveAttribute(
      "href",
      "/super-admin/audit-logs",
    );
  });

  it("shows finance pages only with the view permission and entitlement", () => {
    const permissions = [
      "payments.view",
      "cashbook.view",
      "expenses.view",
      "salaries.view",
      "inventory.view",
      "retail.view",
    ];
    const { rerender } = render(
      <AppShell
        session={session(permissions, false, [{ feature__code: "finance" }])}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Payments" })).toHaveAttribute(
      "href",
      "/payments",
    );
    expect(screen.getByRole("link", { name: "Cash book" })).toHaveAttribute(
      "href",
      "/cash-book",
    );
    expect(screen.getByRole("link", { name: "Expenses" })).toHaveAttribute(
      "href",
      "/expenses",
    );
    expect(screen.getByRole("link", { name: "Salaries" })).toHaveAttribute(
      "href",
      "/salaries",
    );
    expect(screen.getByRole("link", { name: "Inventory" })).toHaveAttribute(
      "href",
      "/inventory",
    );
    expect(screen.getByRole("link", { name: "Retail sales" })).toHaveAttribute(
      "href",
      "/retail-sales",
    );

    rerender(
      <AppShell session={session(permissions)}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Cash book" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Retail sales" }),
    ).not.toBeInTheDocument();
  });
});

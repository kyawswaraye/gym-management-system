import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(tenant)/dashboard/page";
import TrainerDashboardPage from "@/app/(tenant)/pt/dashboard/page";
import ReportsPage from "@/app/(tenant)/reports/page";
import { AppShell } from "@/components/layout/app-shell";
import { requireTenantSession } from "@/lib/auth/session";
import type { CurrentUser } from "@/types/api";

vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  },
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth/session", () => ({
  requireTenantSession: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(() => new Promise(() => undefined)),
}));

vi.mock("@/features/pt/client", () => ({
  getTrainerDashboard: vi.fn(() => new Promise(() => undefined)),
  getTrainerSessionReport: vi.fn(() => new Promise(() => undefined)),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(
  permissions: string[],
  features: string[] = [],
  roles: string[] = [],
): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: { id: "gym-1", name: "Yangon Fitness", timezone: "Asia/Bangkok" },
    gym_membership: {
      id: "membership-1",
      is_owner: roles.includes("OWNER"),
      roles,
    },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

const trainer = session(["reports.view"], ["reports", "pt"], ["TRAINER"]);

describe("trainer dashboard page and navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the trainer dashboard only for a trainer with reports and pt", async () => {
    requireSession.mockResolvedValue(trainer);
    render(await TrainerDashboardPage());
    expect(
      screen.getByRole("region", { name: "Trainer dashboard" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Trainer dashboard" }),
    ).toHaveAttribute("id", "trainer-dashboard-title");
    expect(screen.getByText(/Loading trainer dashboard/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(
      screen.queryByRole("heading", { name: "Owner dashboard" }),
    ).not.toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["reports.view"], ["reports", "pt"], ["OWNER"]),
    );
    await expect(TrainerDashboardPage()).rejects.toThrow(
      "NEXT_REDIRECT:/dashboard",
    );

    requireSession.mockResolvedValue(
      session(["reports.view"], ["reports"], ["TRAINER"]),
    );
    await expect(TrainerDashboardPage()).rejects.toThrow(
      "NEXT_REDIRECT:/dashboard",
    );

    requireSession.mockResolvedValue(
      session(["reports.view"], ["pt"], ["TRAINER"]),
    );
    await expect(TrainerDashboardPage()).rejects.toThrow(
      "NEXT_REDIRECT:/dashboard",
    );

    requireSession.mockResolvedValue(
      session(["members.view"], ["reports", "pt", "members"], ["TRAINER"]),
    );
    await expect(TrainerDashboardPage()).rejects.toThrow(
      "NEXT_REDIRECT:/members",
    );
  });

  it("keeps the owner dashboard and reports closed for a trainer-only session", async () => {
    requireSession.mockResolvedValue(trainer);
    await expect(DashboardPage()).rejects.toThrow(
      "NEXT_REDIRECT:/pt/dashboard",
    );
    await expect(ReportsPage()).rejects.toThrow("NEXT_REDIRECT:/pt/dashboard");

    requireSession.mockResolvedValue(
      session(
        ["reports.view", "members.view"],
        ["reports", "members"],
        ["TRAINER"],
      ),
    );
    await expect(DashboardPage()).rejects.toThrow("NEXT_REDIRECT:/members");

    requireSession.mockResolvedValue(
      session(["reports.view"], ["reports"], ["TRAINER"]),
    );
    render(await DashboardPage());
    expect(
      screen.getByRole("heading", { level: 1, name: "No workspace access" }),
    ).toHaveAttribute("id", "dashboard-title");
    expect(
      screen.queryByRole("heading", { name: "Owner dashboard" }),
    ).not.toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["reports.view"], ["reports"], ["OWNER"]),
    );
    render(await DashboardPage());
    expect(
      screen.getByRole("heading", { level: 1, name: "Owner dashboard" }),
    ).toHaveAttribute("id", "dashboard-title");
    expect(screen.getByText("Live overview")).toBeInTheDocument();
  });

  it("links the trainer dashboard only for that capability", () => {
    const { rerender } = render(
      <AppShell session={trainer}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.getByRole("link", { name: "Trainer dashboard" }),
    ).toHaveAttribute("href", "/pt/dashboard");
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/pt/dashboard");
    expect(
      screen.queryByRole("link", { name: "Dashboard" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Reports" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell
        session={session(
          ["reports.view"],
          ["reports", "pt"],
          ["OWNER", "TRAINER"],
        )}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(
      screen.getByRole("link", { name: "Trainer dashboard" }),
    ).toHaveAttribute("href", "/pt/dashboard");
    expect(screen.getByRole("link", { name: "Reports" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Gym Management" }),
    ).toHaveAttribute("href", "/dashboard");

    rerender(
      <AppShell
        session={session(["reports.view"], ["reports", "pt"], ["RECEPTIONIST"])}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Trainer dashboard" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell session={session(["reports.view"], ["reports"], ["TRAINER"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Trainer dashboard" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Dashboard" }),
    ).not.toBeInTheDocument();
  });
});

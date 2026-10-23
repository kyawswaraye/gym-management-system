import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  operationGet: vi.fn(),
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
    gym: { id: "gym-1", name: "Yangon Fitness" },
    gym_membership: { id: "membership-1", is_owner: false, roles },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("reports page and navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders operational reports for reports.view and keeps trainers closed", async () => {
    requireSession.mockResolvedValue(session(["reports.view"], ["reports"]));
    render(await ReportsPage());
    expect(screen.getByRole("region", { name: "Reports" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Reports" }),
    ).toHaveAttribute("id", "reports-title");
    expect(screen.getByText("Analytics workspace")).toBeInTheDocument();
    expect(screen.getByText(/do not include money/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show membership expiry" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show check-ins" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show PT sessions" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show classes" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show stock movements" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show inventory" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show product sales" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show daily report" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show profit and loss" }),
    ).not.toBeInTheDocument();

    requireSession.mockResolvedValue(session(["reports.financial"]));
    await expect(ReportsPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");

    requireSession.mockResolvedValue(
      session(["reports.view"], ["reports"], ["TRAINER"]),
    );
    await expect(ReportsPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");
  });

  it("renders financial controls when reports.financial is present", async () => {
    requireSession.mockResolvedValue(
      session(["reports.financial", "reports.view"], ["reports"]),
    );
    render(await ReportsPage());
    expect(
      screen.getByRole("button", { name: "Show daily report" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show membership expiry" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show PT sessions" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show classes" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show product sales" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show stock movements" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show inventory" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Choose a date, month, or range."),
    ).toBeInTheDocument();
  });

  it("links reports.view and hides trainer-only callers", () => {
    const { rerender } = render(
      <AppShell session={session(["reports.view"], ["reports"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute(
      "href",
      "/reports",
    );

    rerender(
      <AppShell
        session={session(["reports.view"], ["reports"], ["OWNER", "TRAINER"])}
      >
        <p>Workspace</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Reports" })).toBeInTheDocument();

    rerender(
      <AppShell session={session(["reports.view"], ["reports"], ["TRAINER"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Reports" }),
    ).not.toBeInTheDocument();

    rerender(
      <AppShell session={session(["reports.financial"], ["reports"])}>
        <p>Workspace</p>
      </AppShell>,
    );
    expect(
      screen.queryByRole("link", { name: "Reports" }),
    ).not.toBeInTheDocument();
  });
});

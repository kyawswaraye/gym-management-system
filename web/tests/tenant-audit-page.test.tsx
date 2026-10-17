import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuditLogsPage from "@/app/(tenant)/audit-logs/page";
import DashboardPage from "@/app/(tenant)/dashboard/page";
import { requireTenantSession } from "@/lib/auth/session";
import type { CurrentUser } from "@/types/api";

vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  },
}));

vi.mock("@/lib/auth/session", () => ({
  requireTenantSession: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  listAuditLogs: vi.fn(() => new Promise(() => {})),
  operationGet: vi.fn(),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(permissions: string[], features: string[] = []): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: { id: "gym-1", name: "Yangon Fitness" },
    gym_membership: { id: "membership-1", is_owner: false },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("tenant audit page gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects a principal without audit.view away from audit logs", async () => {
    requireSession.mockResolvedValue(session(["staff.view"]));
    await expect(AuditLogsPage()).rejects.toThrow("NEXT_REDIRECT:/staff");

    requireSession.mockResolvedValue(session(["platform.audit.view"]));
    await expect(AuditLogsPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");

    requireSession.mockResolvedValue(session([]));
    await expect(AuditLogsPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");
  });

  it("renders the audit page for audit.view", async () => {
    requireSession.mockResolvedValue(session(["audit.view"]));
    render(await AuditLogsPage());
    expect(
      screen.getByRole("heading", { name: "Audit logs" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Loading audit events.")).toBeInTheDocument();
  });

  it("sends an audit-only dashboard visit to audit logs", async () => {
    requireSession.mockResolvedValue(session(["audit.view"]));
    await expect(DashboardPage()).rejects.toThrow("NEXT_REDIRECT:/audit-logs");

    requireSession.mockResolvedValue(session(["staff.view", "audit.view"]));
    await expect(DashboardPage()).rejects.toThrow("NEXT_REDIRECT:/staff");
  });

  it("keeps a principal with no tenant home on the empty dashboard", async () => {
    requireSession.mockResolvedValue(session([]));
    render(await DashboardPage());
    expect(
      screen.getByRole("heading", { name: "No workspace access" }),
    ).toBeInTheDocument();
  });
});

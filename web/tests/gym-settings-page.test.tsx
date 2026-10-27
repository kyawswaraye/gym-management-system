import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GymSettingsPage from "@/app/(tenant)/settings/page";
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

vi.mock("@/features/operations/gym-settings-manager", () => ({
  GymSettingsManager: (props: Record<string, boolean>) => (
    <ul>
      {Object.entries(props).map(([key, value]) => (
        <li key={key}>{`${key}:${String(value)}`}</li>
      ))}
    </ul>
  ),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(
  permissions: string[],
  features: string[],
  owner = false,
): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: {
      id: "gym-1",
      name: "Yangon Fitness",
      timezone: "Asia/Yangon",
      currency: "MMK",
    },
    gym_membership: { id: "membership-1", is_owner: owner },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("gym settings page access", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects when the user cannot view the profile or settings", async () => {
    requireSession.mockResolvedValue(session(["receipts.view"], ["finance"]));
    await expect(GymSettingsPage()).rejects.toThrow("NEXT_REDIRECT:");
  });

  it("lets an owner manage settings and reprint when finance receipts are available", async () => {
    requireSession.mockResolvedValue(
      session(
        [
          "gym.view",
          "gym.update",
          "settings.view",
          "settings.manage",
          "receipts.view",
        ],
        ["finance"],
        true,
      ),
    );
    render(await GymSettingsPage());

    expect(screen.getByText("canViewProfile:true")).toBeInTheDocument();
    expect(screen.getByText("canUpdateProfile:true")).toBeInTheDocument();
    expect(screen.getByText("canViewSettings:true")).toBeInTheDocument();
    expect(screen.getByText("canManageSettings:true")).toBeInTheDocument();
    expect(screen.getByText("canViewReceipts:true")).toBeInTheDocument();
  });

  it("withholds receipt actions without receipts.view or the finance feature", async () => {
    requireSession.mockResolvedValue(
      session(["settings.view", "receipts.view"], []),
    );
    const view = render(await GymSettingsPage());
    expect(screen.getByText("canManageSettings:false")).toBeInTheDocument();
    expect(screen.getByText("canViewReceipts:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(session(["settings.view"], ["finance"]));
    view.rerender(await GymSettingsPage());
    expect(screen.getByText("canViewSettings:true")).toBeInTheDocument();
    expect(screen.getByText("canViewReceipts:false")).toBeInTheDocument();
  });
});

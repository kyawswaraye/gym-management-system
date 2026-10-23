import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MembersPage from "@/app/(tenant)/members/page";
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

vi.mock("@/features/operations/members-manager", () => ({
  MembersManager: ({
    canArchive,
    canCreate,
    canRestore,
    defaultDate,
  }: {
    canArchive: boolean;
    canCreate: boolean;
    canRestore: boolean;
    defaultDate: string;
  }) => (
    <section>
      <p>archive:{String(canArchive)}</p>
      <p>create:{String(canCreate)}</p>
      <p>restore:{String(canRestore)}</p>
      <p>date:{defaultDate}</p>
    </section>
  ),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(permissions: string[], features: string[]): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: { id: "gym-1", name: "Yangon Fitness", timezone: "UTC" },
    gym_membership: { id: "membership-1", is_owner: false },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("members page capability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects without members.view and the members feature", async () => {
    requireSession.mockResolvedValue(session(["members.restore"], ["members"]));
    await expect(MembersPage()).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(session(["members.view"], ["finance"]));
    await expect(MembersPage()).rejects.toThrow("NEXT_REDIRECT:");
  });

  it("passes restore access only from members.restore", async () => {
    requireSession.mockResolvedValue(
      session(["members.view", "members.create"], ["members"]),
    );
    const { rerender } = render(await MembersPage());
    expect(screen.getByRole("region", { name: "Members" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Members" }),
    ).toHaveAttribute("id", "members-title");
    expect(screen.getByText("Member directory")).toBeInTheDocument();
    expect(
      screen.getByText(/open Archived to find members who were archived/),
    ).toBeInTheDocument();
    expect(screen.getByText("create:true")).toBeInTheDocument();
    expect(screen.getByText("archive:false")).toBeInTheDocument();
    expect(screen.getByText("restore:false")).toBeInTheDocument();
    expect(screen.getByText(/^date:\d{4}-\d{2}-\d{2}$/)).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "members.restore"], ["members"]),
    );
    rerender(await MembersPage());
    expect(screen.getByText("create:false")).toBeInTheDocument();
    expect(screen.getByText("archive:false")).toBeInTheDocument();
    expect(screen.getByText("restore:true")).toBeInTheDocument();
  });

  it("passes archive access only from members.archive", async () => {
    requireSession.mockResolvedValue(
      session(["members.view", "members.restore"], ["members"]),
    );
    const { rerender } = render(await MembersPage());
    expect(screen.getByText("archive:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "members.archive"], ["members"]),
    );
    rerender(await MembersPage());
    expect(screen.getByText("archive:true")).toBeInTheDocument();
    expect(screen.getByText("restore:false")).toBeInTheDocument();
  });
});

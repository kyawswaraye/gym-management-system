import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MemberDetailPage from "@/app/(tenant)/members/[id]/page";
import { requireTenantSession } from "@/lib/auth/session";
import type { CurrentUser } from "@/types/api";

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";

vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  },
}));

vi.mock("@/lib/auth/session", () => ({
  requireTenantSession: vi.fn(),
}));

vi.mock("@/features/operations/member-detail", () => ({
  MemberDetail: ({
    canUpdate,
    canViewPayments,
    memberId,
  }: {
    canUpdate: boolean;
    canViewPayments: boolean;
    memberId: string;
  }) => (
    <section>
      <p>member:{memberId}</p>
      <p>update:{String(canUpdate)}</p>
      <p>payments:{String(canViewPayments)}</p>
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
    gym: { id: "gym-1", name: "Yangon Fitness" },
    gym_membership: { id: "membership-1", is_owner: false },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("member detail page capability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects without members.view and the members feature", async () => {
    requireSession.mockResolvedValue(
      session(["payments.view"], ["members", "finance"]),
    );
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(session(["members.view"], ["finance"]));
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");
  });

  it("passes payment history access only when both gates are present", async () => {
    requireSession.mockResolvedValue(
      session(["members.view", "members.update"], ["members"]),
    );
    const { rerender } = render(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText(`member:${MEMBER_ID}`)).toBeInTheDocument();
    expect(screen.getByText("update:true")).toBeInTheDocument();
    expect(screen.getByText("payments:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "payments.view"], ["members"]),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("payments:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "payments.view"], ["members", "finance"]),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("payments:true")).toBeInTheDocument();
    expect(screen.getByText("update:false")).toBeInTheDocument();
  });
});

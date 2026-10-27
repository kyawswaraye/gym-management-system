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
    canArchive,
    canRestore,
    canUpdate,
    canViewPayments,
    canViewReceipts = false,
    canViewPhoto,
    memberId,
  }: {
    canArchive: boolean;
    canRestore: boolean;
    canUpdate: boolean;
    canViewPayments: boolean;
    canViewReceipts?: boolean;
    canViewPhoto: boolean;
    memberId: string;
  }) => (
    <section>
      <p>member:{memberId}</p>
      <p>archive:{String(canArchive)}</p>
      <p>restore:{String(canRestore)}</p>
      <p>update:{String(canUpdate)}</p>
      <p>payments:{String(canViewPayments)}</p>
      <p>receipts:{String(canViewReceipts)}</p>
      <p>view-photo:{String(canViewPhoto)}</p>
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
    expect(
      screen.getByRole("region", { name: "Member profile" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Member profile" }),
    ).toHaveAttribute("id", "member-title");
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
    expect(screen.getByText("receipts:false")).toBeInTheDocument();
    expect(screen.getByText("update:false")).toBeInTheDocument();
    expect(screen.getByText("archive:false")).toBeInTheDocument();
    expect(screen.getByText("restore:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(
        ["members.view", "payments.view", "receipts.view"],
        ["members"],
      ),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("receipts:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(
        ["members.view", "payments.view", "receipts.view"],
        ["members", "finance"],
      ),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("payments:true")).toBeInTheDocument();
    expect(screen.getByText("receipts:true")).toBeInTheDocument();
  });

  it("passes archive access only from members.archive", async () => {
    requireSession.mockResolvedValue(
      session(
        ["members.view", "members.update", "payments.view"],
        ["members", "finance"],
      ),
    );
    const { rerender } = render(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("archive:false")).toBeInTheDocument();
    expect(screen.getByText("update:true")).toBeInTheDocument();
    expect(screen.getByText("payments:true")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "members.archive"], ["members"]),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("archive:true")).toBeInTheDocument();
    expect(screen.getByText("restore:false")).toBeInTheDocument();
    expect(screen.getByText("update:false")).toBeInTheDocument();
    expect(screen.getByText("payments:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(session(["members.archive"], ["members"]));
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(
      session(["members.view", "members.archive"], ["finance"]),
    );
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");
  });

  it("passes restore access only from members.restore", async () => {
    requireSession.mockResolvedValue(
      session(["members.view", "members.archive"], ["members"]),
    );
    const { rerender } = render(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("restore:false")).toBeInTheDocument();
    expect(screen.getByText("archive:true")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["members.view", "members.restore"], ["members"]),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("restore:true")).toBeInTheDocument();
    expect(screen.getByText("archive:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(session(["members.restore"], ["members"]));
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");
  });

  it("derives photo capabilities from live permissions without a new page gate", async () => {
    requireSession.mockResolvedValue(
      session(
        ["members.view", "files.view", "files.upload", "files.delete"],
        ["members"],
      ),
    );
    const { rerender } = render(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("view-photo:true")).toBeInTheDocument();
    expect(screen.getByText("update:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(
        ["members.view", "members.update", "files.upload", "files.delete"],
        ["members"],
      ),
    );
    rerender(
      await MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    );
    expect(screen.getByText("update:true")).toBeInTheDocument();
    expect(screen.getByText("view-photo:false")).toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["files.view", "files.upload", "members.update"], ["members"]),
    );
    await expect(
      MemberDetailPage({ params: Promise.resolve({ id: MEMBER_ID }) }),
    ).rejects.toThrow("NEXT_REDIRECT:");
  });
});

import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClassesPage from "@/app/(tenant)/classes/page";
import CheckInsPage from "@/app/(tenant)/check-ins/page";
import CommissionsPage from "@/app/(tenant)/pt/commissions/page";
import MemberPackagesPage from "@/app/(tenant)/pt/member-packages/page";
import PackagesPage from "@/app/(tenant)/pt/packages/page";
import PtBookingsPage from "@/app/(tenant)/pt/bookings/page";
import PtSalesPage from "@/app/(tenant)/pt/sales/page";
import TrainersPage from "@/app/(tenant)/pt/trainers/page";
import { requireTenantSession } from "@/lib/auth/session";
import type { CurrentUser } from "@/types/api";

vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  },
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth/session", () => ({
  requireTenantSession: vi.fn(),
}));

vi.mock("@/features/pt/client", () => ({
  listTrainers: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listAssignments: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listAssignedMembers: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listServicePackages: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listClassTypes: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listMemberPackages: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listPtSales: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listPtCommissions: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listPtBookings: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listClassSchedules: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listClassOccurrences: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listClassBookings: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
}));

vi.mock("@/features/operations/client", () => ({
  listCheckIns: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listMembers: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  listStaff: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
  OperationError: class OperationError extends Error {},
}));

vi.mock("@/features/finance/client", () => ({
  listCashSessions: vi.fn(() =>
    Promise.resolve({ data: [], meta: { has_more: false, next_after: null } }),
  ),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(
  permissions: string[],
  roles: string[] = ["OWNER"],
  features: string[] = ["pt", "members", "memberships"],
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
      timezone: "UTC",
      currency: "MMK",
    },
    gym_membership: {
      id: "membership-1",
      is_owner: roles.includes("OWNER"),
      roles,
      status: "ACTIVE",
    },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as unknown as CurrentUser;
}

describe("phase 5 page gates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects when the pt entitlement or view permission is missing", async () => {
    requireSession.mockResolvedValue(
      session(["pt.assignments.view"], ["OWNER"], []),
    );
    await expect(TrainersPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");

    requireSession.mockResolvedValue(
      session(["members.view"], ["RECEPTIONIST"], ["members"]),
    );
    await expect(PackagesPage()).rejects.toThrow("NEXT_REDIRECT:/members");
    await expect(PtSalesPage()).rejects.toThrow("NEXT_REDIRECT:/members");
    await expect(CommissionsPage()).rejects.toThrow("NEXT_REDIRECT:/members");
    await expect(ClassesPage()).rejects.toThrow("NEXT_REDIRECT:/members");
  });

  it("renders each workspace for the matching view permission", async () => {
    requireSession.mockResolvedValue(
      session(["pt.assignments.view"], ["TRAINER"]),
    );
    render(await TrainersPage());
    expect(
      screen.getByRole("region", { name: "Trainers" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Trainers" }),
    ).toHaveAttribute("id", "trainers-title");
    expect(screen.getByText("Personal training")).toBeInTheDocument();
    expect(
      screen.getByText(/For personal training only\. Assign a member/),
    ).toBeInTheDocument();
    expect(screen.queryByText("◇")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["pt.assignments.view", "pt.assignments.manage"], ["MANAGER"]),
    );
    render(await TrainersPage());
    expect(
      await screen.findByRole("button", { name: "Create assignment" }),
    ).toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(session(["pt.schedule.view"], ["DESK"]));
    render(await PackagesPage());
    expect(
      screen.getByRole("region", { name: "Packages" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Packages" }),
    ).toHaveAttribute("id", "packages-title");
    expect(screen.getByText(/pricing in one place/)).toBeInTheDocument();
    expect(screen.queryByText("◫")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create package" }),
    ).not.toBeInTheDocument();
    cleanup();

    render(await MemberPackagesPage());
    expect(
      screen.getByRole("heading", { name: "Member packages" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Grant package" }),
    ).not.toBeInTheDocument();

    requireSession.mockResolvedValue(
      session(["pt.schedule.view", "pt.sessions.adjust"], ["OWNER"]),
    );
    render(await MemberPackagesPage());
    expect(
      await screen.findByRole("button", { name: "Grant package" }),
    ).toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(
        ["pt.schedule.view", "pt.sessions.adjust"],
        ["TRAINER", "MANAGER"],
      ),
    );
    render(await MemberPackagesPage());
    expect(
      screen.queryByRole("button", { name: "Grant package" }),
    ).not.toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["pt.sales.view"], ["RECEPTIONIST"]),
    );
    render(await PtSalesPage());
    expect(
      screen.getByRole("heading", { name: "PT sales" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create draft" }),
    ).not.toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["pt.commissions.view"], ["ACCOUNTANT"]),
    );
    render(await CommissionsPage());
    expect(
      screen.getByRole("heading", { name: "Commissions" }),
    ).toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["pt.schedule.view"], ["TRAINER"]),
    );
    render(await PtBookingsPage());
    expect(
      screen.getByRole("heading", { name: "PT bookings" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Book session" }),
    ).not.toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["classes.view"], ["RECEPTIONIST"]),
    );
    render(await ClassesPage());
    expect(
      screen.getByRole("heading", { name: "Classes" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create class type" }),
    ).not.toBeInTheDocument();
  });

  it("shows trainer scan only for a protected trainer who can create check-ins", async () => {
    requireSession.mockResolvedValue(
      session(
        ["checkins.view", "checkins.create"],
        ["TRAINER"],
        ["members", "memberships"],
      ),
    );
    render(await CheckInsPage());
    expect(
      await screen.findByRole("heading", { name: "Trainer QR scan" }),
    ).toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(
        ["checkins.view", "checkins.create"],
        ["DESK"],
        ["members", "memberships"],
      ),
    );
    render(await CheckInsPage());
    expect(
      screen.queryByRole("heading", { name: "Trainer QR scan" }),
    ).not.toBeInTheDocument();
    cleanup();

    requireSession.mockResolvedValue(
      session(["checkins.view"], ["TRAINER"], ["members", "memberships"]),
    );
    render(await CheckInsPage());
    expect(
      screen.queryByRole("heading", { name: "Trainer QR scan" }),
    ).not.toBeInTheDocument();
  });
});

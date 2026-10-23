import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CheckInDisplayPage from "@/app/(tenant)/check-ins/display/page";
import CheckInsPage from "@/app/(tenant)/check-ins/page";
import {
  issueCheckInChallenge,
  listCheckIns,
} from "@/features/operations/client";
import {
  checkInViewValue,
  checkInWorkspaceMode,
} from "@/features/operations/check-ins-mode";
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

vi.mock("qrcode", () => ({
  default: {
    toDataURL: vi.fn(async () => "data:image/png;base64,qr"),
  },
}));

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {},
  issueCheckInChallenge: vi.fn(),
  listCheckIns: vi.fn(),
  listMemberCheckIns: vi.fn(),
  listMembers: vi.fn(),
  createCheckIn: vi.fn(),
  checkOutCheckIn: vi.fn(),
}));

const requireSession = vi.mocked(requireTenantSession);

function session(
  permissions: string[],
  features: string[] = ["members", "memberships"],
  roles: string[] = ["RECEPTIONIST"],
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
    gym_membership: {
      id: "membership-1",
      is_owner: roles.includes("OWNER"),
      roles,
      status: "ACTIVE",
    },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as unknown as CurrentUser;
}

function issuedChallenge() {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    payload: "gms-checkin:v1:secret-value",
    display_location: "Front desk",
    issued_at: "2026-10-23T12:00:00.000Z",
    expires_at: "2026-10-23T12:00:30.000Z",
    max_uses: 1,
    status: "ACTIVE",
  };
}

describe("check-in workspace mode", () => {
  it("selects an authorized mode and ignores an unauthorized QR query", () => {
    expect(
      checkInWorkspaceMode({ canDesk: false, canQr: false, view: "qr" }),
    ).toBeNull();
    expect(
      checkInWorkspaceMode({ canDesk: true, canQr: false, view: "qr" }),
    ).toBe("desk");
    expect(checkInWorkspaceMode({ canDesk: false, canQr: true })).toBe("qr");
    expect(
      checkInWorkspaceMode({ canDesk: false, canQr: true, view: "desk" }),
    ).toBe("qr");
    expect(checkInWorkspaceMode({ canDesk: true, canQr: true })).toBe("desk");
    expect(
      checkInWorkspaceMode({ canDesk: true, canQr: true, view: "qr" }),
    ).toBe("qr");
    expect(
      checkInWorkspaceMode({
        canDesk: true,
        canQr: true,
        view: checkInViewValue(["qr"]),
      }),
    ).toBe("qr");
    expect(
      checkInWorkspaceMode({
        canDesk: true,
        canQr: true,
        view: checkInViewValue(["desk", "qr"]),
      }),
    ).toBe("desk");
    expect(checkInViewValue(undefined)).toBeNull();
  });
});

describe("check-ins page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCheckIns).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(issueCheckInChallenge).mockResolvedValue(issuedChallenge());
  });

  it("redirects when neither check-in capability is available", async () => {
    requireSession.mockResolvedValue(session(["members.view"], ["members"]));
    await expect(CheckInsPage()).rejects.toThrow("NEXT_REDIRECT:/members");

    requireSession.mockResolvedValue(
      session(["checkins.view", "checkins.challenge.issue"], ["members"]),
    );
    await expect(CheckInsPage()).rejects.toThrow("NEXT_REDIRECT:/dashboard");
  });

  it("shows the desk directly when that is the only capability", async () => {
    requireSession.mockResolvedValue(
      session(["checkins.view", "checkins.create", "checkins.update"]),
    );
    render(
      await CheckInsPage({ searchParams: Promise.resolve({ view: "qr" }) }),
    );
    expect(
      screen.getByRole("region", { name: "Check-ins" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Check-ins" }),
    ).toHaveAttribute("id", "check-ins-title");
    expect(screen.getByText("Front desk")).toBeInTheDocument();
    expect(screen.queryByText("◉")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Search a member, record a manual visit, and check that visit out.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(
      await screen.findByText("Nobody is checked in."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Search active members")).toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(issueCheckInChallenge).not.toHaveBeenCalled();
    expect(listCheckIns).toHaveBeenCalled();
  });

  it("shows the QR display directly when that is the only capability", async () => {
    requireSession.mockResolvedValue(session(["checkins.challenge.issue"]));
    render(await CheckInsPage());
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Show this rotating code at the desk. Members cannot scan it from a staff login.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Display location")).toBeInTheDocument();
    expect(
      await screen.findByRole("img", { name: "Current onsite check-in code" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Search active members"),
    ).not.toBeInTheDocument();
    expect(listCheckIns).not.toHaveBeenCalled();
    expect(
      screen.queryByText("gms-checkin:v1:secret-value"),
    ).not.toBeInTheDocument();
  });

  it("opens the requested mode when both capabilities are available", async () => {
    requireSession.mockResolvedValue(
      session(["checkins.view", "checkins.challenge.issue", "checkins.create"]),
    );
    render(await CheckInsPage());
    expect(
      screen.getByText(
        "Record a visit at the desk, or show a rotating QR code for members to scan.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Desk check-ins" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "QR display" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(
      await screen.findByText("Nobody is checked in."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(issueCheckInChallenge).not.toHaveBeenCalled();
    cleanup();
    vi.mocked(listCheckIns).mockClear();

    render(
      await CheckInsPage({ searchParams: Promise.resolve({ view: "qr" }) }),
    );
    expect(screen.getByRole("tab", { name: "QR display" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Desk check-ins" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByLabelText("Display location")).toBeInTheDocument();
    expect(
      await screen.findByRole("img", { name: "Current onsite check-in code" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Search active members"),
    ).not.toBeInTheDocument();
    expect(listCheckIns).not.toHaveBeenCalled();
  });

  it("redirects the legacy QR route after the permission check", async () => {
    requireSession.mockResolvedValue(session(["checkins.challenge.issue"]));
    await expect(CheckInDisplayPage()).rejects.toThrow(
      "NEXT_REDIRECT:/check-ins?view=qr",
    );

    requireSession.mockResolvedValue(
      session(["checkins.view", "members.view"]),
    );
    await expect(CheckInDisplayPage()).rejects.toThrow(
      "NEXT_REDIRECT:/members",
    );
  });
});

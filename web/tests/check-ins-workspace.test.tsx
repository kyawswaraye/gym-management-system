import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  issueCheckInChallenge,
  listCheckIns,
} from "@/features/operations/client";
import { CheckInsWorkspace } from "@/features/operations/check-ins-workspace";
import type { CheckInWorkspaceMode } from "@/features/operations/check-ins-mode";

const replace = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh: vi.fn() }),
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

function workspace(mode: CheckInWorkspaceMode, canDesk = true, canQr = true) {
  return (
    <CheckInsWorkspace
      canCreate
      canDesk={canDesk}
      canQr={canQr}
      canUpdate
      membershipId="membership-1"
      mode={mode}
      timeZone="Asia/Yangon"
    />
  );
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("check-ins workspace", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function primeClients() {
    vi.mocked(listCheckIns).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(issueCheckInChallenge).mockImplementation(async () => ({
      id: "22222222-2222-4222-8222-222222222222",
      payload: "gms-checkin:v1:secret-value",
      display_location: "Front desk",
      issued_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 20_000).toISOString(),
      max_uses: 1,
      status: "ACTIVE",
    }));
  }

  it("keeps the inactive mode unmounted and stops QR timers on switch", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    primeClients();

    render(workspace("desk"));
    await flush();
    expect(screen.getByLabelText("Search active members")).toBeInTheDocument();
    expect(
      screen.getByText("Search, check in, and check out."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Rotating code on this screen."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(issueCheckInChallenge).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "QR display" }));
    await flush();
    expect(replace).toHaveBeenCalledWith("/check-ins?view=qr", {
      scroll: false,
    });
    expect(
      screen.getByRole("button", { name: "Update location" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Front desk")).toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Search active members"),
    ).not.toBeInTheDocument();
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("tab", { name: "QR display" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.click(screen.getByRole("tab", { name: "Desk check-ins" }));
    expect(replace).toHaveBeenCalledWith("/check-ins", { scroll: false });
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Search active members")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(1);
  });

  it("follows an authorized mode passed back from the page", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    primeClients();

    const view = render(workspace("qr"));
    await flush();
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(1);
    view.rerender(workspace("desk"));
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Search active members")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(1);
  });

  it("moves between modes from the keyboard", async () => {
    primeClients();
    render(workspace("desk"));
    await flush();
    const desk = screen.getByRole("tab", { name: "Desk check-ins" });
    desk.focus();
    fireEvent.keyDown(desk, { key: "ArrowRight" });
    await flush();
    expect(replace).toHaveBeenCalledWith("/check-ins?view=qr", {
      scroll: false,
    });
    expect(screen.getByRole("tab", { name: "QR display" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Desk check-ins" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
  });

  it("omits the switch when only one mode is authorized", async () => {
    primeClients();
    const desk = render(workspace("qr", true, false));
    await flush();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Search active members")).toBeInTheDocument();
    expect(issueCheckInChallenge).not.toHaveBeenCalled();
    desk.unmount();

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(workspace("desk", false, true));
    await flush();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Update location" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Front desk")).toBeInTheDocument();
    expect(screen.queryByLabelText("Display location")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Search active members"),
    ).not.toBeInTheDocument();
  });
});

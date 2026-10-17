import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformAuditLog } from "@/features/platform/audit-log-manager";
import { platformGetCollection } from "@/features/platform/client";
import type { PlatformAuditItem } from "@/types/api";

vi.mock("@/features/platform/client", () => ({
  platformGetCollection: vi.fn(),
}));

const getAudit = vi.mocked(platformGetCollection);

const gymId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

const event: PlatformAuditItem = {
  id: "34485f4c-d7df-4244-bae6-072868cc111a",
  actor_user_id: "f3aa57e5-90ce-48da-afab-1735ee42ace1",
  actor_email: "admin@example.test",
  target_gym_id: gymId,
  target_gym_code: "YGN-01",
  target_type: "SubscriptionPlan",
  target_id: "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
  action: `<img src=x onerror=alert(1)>`,
  module: "platform",
  outcome: "SUCCESS",
  old_values: {
    access_token: "super-secret-token",
    note: "<script>alert(1)</script>",
  },
  new_values: { refresh_token: "refresh-secret", name: "Growth" },
  request_id: "req_test",
  occurred_at: "2026-10-01T00:00:00Z",
};

const nextEvent: PlatformAuditItem = {
  ...event,
  id: "45596f5d-e8e0-4355-cbf7-183979dd222b",
  action: "SUBSCRIPTION_PLAN_UPDATED",
  old_values: { name: "Growth" },
  new_values: { name: "Growth Plus" },
};

function list(data: PlatformAuditItem[], nextAfter: string | null) {
  return {
    success: true as const,
    data,
    meta: { has_more: nextAfter !== null, next_after: nextAfter },
    message: "Success",
  };
}

describe("platform audit log", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not request audit events without permission", async () => {
    render(<PlatformAuditLog canView={false} />);

    expect(
      screen.getByText("You do not have access to platform audit logs."),
    ).toBeInTheDocument();
    await waitFor(() => expect(getAudit).not.toHaveBeenCalled());
  });

  it("escapes untrusted values and hides credential payloads", async () => {
    getAudit.mockResolvedValue(list([event], null));
    const { container } = render(<PlatformAuditLog canView />);

    expect(await screen.findByText(event.action)).toBeInTheDocument();
    expect(screen.getByText("admin@example.test")).toBeInTheDocument();
    expect(screen.getAllByText(/\[REDACTED\]/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/<script>alert\(1\)<\/script>/),
    ).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("<img");
    expect(container.innerHTML).not.toContain("<script");
    expect(container.innerHTML).not.toContain("super-secret-token");
    expect(container.innerHTML).not.toContain("refresh-secret");
    expect(screen.queryByLabelText(/password|token|secret/i)).toBeNull();
  });

  it("applies strict filters and an opaque cursor", async () => {
    const cursor = "cursor.1:abc";
    getAudit
      .mockResolvedValueOnce(list([event], "initial.cursor"))
      .mockResolvedValueOnce(list([event], cursor))
      .mockResolvedValueOnce(list([nextEvent], null));
    render(<PlatformAuditLog canView />);
    await screen.findByText(event.action);

    expect(getAudit).toHaveBeenCalledWith("admin/audit-logs?limit=25");
    fireEvent.change(screen.getByLabelText("Target gym ID"), {
      target: { value: gymId.toUpperCase() },
    });
    fireEvent.change(screen.getByLabelText("Action"), {
      target: { value: "SUBSCRIPTION_PLAN_UPDATED" },
    });
    fireEvent.change(screen.getByLabelText("Outcome"), {
      target: { value: "DENIED" },
    });
    fireEvent.change(screen.getByLabelText("Page size"), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(getAudit).toHaveBeenCalledTimes(2));
    const filtered = new URL(
      String(getAudit.mock.calls[1]?.[0]),
      "https://app.example",
    );
    expect(filtered.searchParams.get("target_gym_id")).toBe(gymId);
    expect(filtered.searchParams.get("action")).toBe(
      "SUBSCRIPTION_PLAN_UPDATED",
    );
    expect(filtered.searchParams.get("outcome")).toBe("DENIED");
    expect(filtered.searchParams.get("limit")).toBe("10");
    expect(filtered.searchParams.get("after")).toBeNull();
    expect(filtered.searchParams.get("access_token")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(getAudit).toHaveBeenCalledTimes(3));
    const more = new URL(
      String(getAudit.mock.calls[2]?.[0]),
      "https://app.example",
    );
    expect(more.searchParams.get("after")).toBe(cursor);
    expect(more.searchParams.get("target_gym_id")).toBe(gymId);
    expect(screen.queryByText(cursor)).not.toBeInTheDocument();
    expect(
      await screen.findByText("SUBSCRIPTION_PLAN_UPDATED"),
    ).toBeInTheDocument();
  });

  it("rejects an invalid gym id or page size without another request", async () => {
    getAudit.mockResolvedValue(list([event], null));
    render(<PlatformAuditLog canView />);
    await screen.findByText(event.action);

    fireEvent.change(screen.getByLabelText("Target gym ID"), {
      target: { value: "not-a-uuid" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid target gym ID.",
    );

    fireEvent.change(screen.getByLabelText("Target gym ID"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Page size"), {
      target: { value: "101" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a page size from 1 to 100.",
    );
    expect(getAudit).toHaveBeenCalledTimes(1);
  });

  it("shows empty and error states", async () => {
    getAudit.mockResolvedValueOnce(list([], null));
    const { rerender } = render(<PlatformAuditLog canView />);
    expect(
      await screen.findByText("No audit events found."),
    ).toBeInTheDocument();

    getAudit.mockRejectedValueOnce(new Error("Audit unavailable."));
    rerender(<PlatformAuditLog canView={false} />);
    rerender(<PlatformAuditLog canView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Audit unavailable.",
    );
    expect(
      screen.queryByText("No audit events found."),
    ).not.toBeInTheDocument();
  });
});

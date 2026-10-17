import { readFileSync } from "node:fs";
import path from "node:path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TenantAuditLog } from "@/features/operations/audit-log-manager";
import { listAuditLogs } from "@/features/operations/client";
import type { TenantAuditItem, TenantAuditMeta } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  listAuditLogs: vi.fn(),
}));

const loadAudit = vi.mocked(listAuditLogs);
const targetId = "f52030c1-18af-4e18-a1ec-50997d892ef4";

const event = {
  id: "34485f4c-d7df-4244-bae6-072868cc111a",
  actor_membership_id: "11111111-1111-4111-8111-111111111111",
  impersonator_membership_id: null,
  action: "<img src=x onerror=alert(1)>",
  module: "roles",
  target_type: "Role",
  target_id: targetId,
  old_values: {
    access_token: "super-secret-token",
    note: "<script>alert(1)</script>",
    nested: {
      password: "hunter2",
      email: "hidden@example.test",
      gym_id: "gym-should-not-show",
      ip_address: "203.0.113.10",
      user_agent: "SecretBrowser/1.0",
      safe: "kept-note",
    },
  },
  new_values: { refresh_token: "refresh-secret", name: "Front desk" },
  request_id: "req_test",
  outcome: "SUCCESS",
  occurred_at: "2026-10-17T00:00:00Z",
  actor_email: "hidden@example.test",
  gym_id: "gym-should-not-show",
  ip_address: "203.0.113.10",
  user_agent: "SecretBrowser/1.0",
  access_token: "super-secret-token",
  password: "hunter2",
} as TenantAuditItem;

const nextEvent: TenantAuditItem = {
  ...event,
  id: "45596f5d-e8e0-4355-cbf7-183979dd222b",
  action: "ROLE_UPDATED",
  old_values: { name: "Front desk" },
  new_values: { name: "Reception" },
};

function page(
  data: TenantAuditItem[],
  scope: TenantAuditMeta["scope"],
  nextAfter: string | null,
) {
  return {
    data,
    meta: {
      has_more: nextAfter !== null,
      next_after: nextAfter,
      scope,
    },
  };
}

describe("tenant audit log", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders old and new JSON as escaped text and hides forbidden fields", async () => {
    loadAudit.mockResolvedValue(page([event], "SELF", null));
    const source = readFileSync(
      path.resolve(
        process.cwd(),
        "src/features/operations/audit-log-manager.tsx",
      ),
      "utf8",
    );
    expect(source).not.toContain("dangerouslySetInnerHTML");
    expect(source).toContain("<pre");
    expect(source).not.toContain("actor_email");
    expect(source).not.toContain("gym_id");
    expect(source).not.toContain("ip_address");
    expect(source).not.toContain("user_agent");

    const { container } = render(<TenantAuditLog canView />);
    expect(await screen.findByText("Your actions only")).toBeInTheDocument();
    expect(
      screen.getByText("Showing only actions recorded for your membership."),
    ).toBeInTheDocument();
    expect(screen.queryByText("All gym events")).not.toBeInTheDocument();
    expect(screen.getByText(event.action)).toBeInTheDocument();
    expect(
      screen.getByText(/<script>alert\(1\)<\/script>/),
    ).toBeInTheDocument();
    expect(screen.getByText(/kept-note/)).toBeInTheDocument();
    expect(screen.getAllByText(/\[REDACTED\]/).length).toBeGreaterThan(0);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("pre")).not.toBeNull();
    expect(container.innerHTML).not.toContain("<img");
    expect(container.innerHTML).not.toContain("<script");
    expect(container.innerHTML).not.toContain("super-secret-token");
    expect(container.innerHTML).not.toContain("refresh-secret");
    expect(container.innerHTML).not.toContain("hunter2");
    expect(container.innerHTML).not.toContain("hidden@example.test");
    expect(container.innerHTML).not.toContain("gym-should-not-show");
    expect(container.innerHTML).not.toContain("203.0.113.10");
    expect(container.innerHTML).not.toContain("SecretBrowser/1.0");
    expect(screen.queryByLabelText(/email/i)).toBeNull();
    expect(screen.queryByLabelText(/gym id/i)).toBeNull();
    expect(screen.queryByLabelText(/user agent/i)).toBeNull();
    expect(screen.queryByLabelText(/ip address/i)).toBeNull();
    expect(screen.queryByLabelText(/password|token|secret/i)).toBeNull();
  });

  it("labels a gym-scoped page as all gym events", async () => {
    loadAudit.mockResolvedValue(page([event], "GYM", null));
    render(<TenantAuditLog canView />);
    expect(await screen.findByText("All gym events")).toBeInTheDocument();
    expect(screen.queryByText("Your actions only")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Showing only actions recorded for your membership."),
    ).not.toBeInTheDocument();
  });

  it("applies filters and loads the next opaque page", async () => {
    const cursor = "cursor.1:abc";
    loadAudit
      .mockResolvedValueOnce(page([event], "GYM", "initial.cursor"))
      .mockResolvedValueOnce(page([event], "GYM", cursor))
      .mockResolvedValueOnce(page([nextEvent], "SELF", null));
    render(<TenantAuditLog canView />);
    await screen.findByText(event.action);
    expect(loadAudit).toHaveBeenCalledWith({
      action: "",
      module: "",
      outcome: "",
      targetType: "",
      targetId: "",
      limit: "25",
    });
    expect(screen.queryByText("initial.cursor")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Action"), {
      target: { value: "ROLE_UPDATED" },
    });
    fireEvent.change(screen.getByLabelText("Module"), {
      target: { value: "roles" },
    });
    fireEvent.change(screen.getByLabelText("Outcome"), {
      target: { value: "DENIED" },
    });
    fireEvent.change(screen.getByLabelText("Target type"), {
      target: { value: "Role" },
    });
    fireEvent.change(screen.getByLabelText("Target ID"), {
      target: { value: targetId.toUpperCase() },
    });
    fireEvent.change(screen.getByLabelText("Page size"), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(loadAudit).toHaveBeenCalledTimes(2));
    expect(loadAudit).toHaveBeenLastCalledWith({
      action: "ROLE_UPDATED",
      module: "roles",
      outcome: "DENIED",
      targetType: "Role",
      targetId: targetId.toUpperCase(),
      limit: "10",
    });

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(loadAudit).toHaveBeenCalledTimes(3));
    expect(loadAudit).toHaveBeenLastCalledWith({
      action: "ROLE_UPDATED",
      module: "roles",
      outcome: "DENIED",
      targetType: "Role",
      targetId: targetId.toUpperCase(),
      limit: "10",
      after: cursor,
    });
    expect(screen.queryByText(cursor)).not.toBeInTheDocument();
    expect(await screen.findByText("ROLE_UPDATED")).toBeInTheDocument();
    expect(await screen.findByText("Your actions only")).toBeInTheDocument();
  });

  it("rejects an invalid target or page size without another request", async () => {
    loadAudit.mockResolvedValue(page([event], "SELF", null));
    render(<TenantAuditLog canView />);
    await screen.findByText(event.action);

    fireEvent.change(screen.getByLabelText("Target ID"), {
      target: { value: "not-a-uuid" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid target ID.",
    );

    fireEvent.change(screen.getByLabelText("Target ID"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Page size"), {
      target: { value: "101" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a page size from 1 to 100.",
    );
    expect(loadAudit).toHaveBeenCalledTimes(1);
  });

  it("does not request events without permission", async () => {
    render(<TenantAuditLog canView={false} />);
    expect(
      screen.getByText("You do not have access to audit logs."),
    ).toBeInTheDocument();
    await waitFor(() => expect(loadAudit).not.toHaveBeenCalled());
  });

  it("shows loading, empty, and error states", async () => {
    let resolveLoad: (value: ReturnType<typeof page>) => void = () => {};
    loadAudit.mockReturnValue(
      new Promise((resolve) => {
        resolveLoad = resolve;
      }),
    );
    const { rerender } = render(<TenantAuditLog canView />);
    expect(screen.getByText("Loading audit events.")).toBeInTheDocument();
    resolveLoad(page([], "SELF", null));
    expect(
      await screen.findByText("No audit events found."),
    ).toBeInTheDocument();
    expect(screen.getByText("Your actions only")).toBeInTheDocument();
    expect(screen.queryByText("All gym events")).not.toBeInTheDocument();

    loadAudit.mockRejectedValueOnce(new Error("Audit unavailable."));
    rerender(<TenantAuditLog canView={false} />);
    rerender(<TenantAuditLog canView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Audit unavailable.",
    );
    expect(
      screen.queryByText("No audit events found."),
    ).not.toBeInTheDocument();
  });
});

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { operationGet, operationMutation } from "@/features/operations/client";
import { OwnersManager } from "@/features/operations/owners-manager";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
  operationMutation: vi.fn(),
}));

describe("Owner management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(operationGet).mockResolvedValue([
      {
        gym_membership_id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
        user_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
        email: "owner@example.test",
        first_name: "First",
        last_name: "Owner",
        phone: "",
        status: "ACTIVE",
        is_owner: true,
        authz_version: 1,
        invited_at: null,
        activated_at: "2026-09-30T00:00:00Z",
      },
    ]);
    vi.mocked(operationMutation).mockResolvedValue({});
  });

  it("shows Owner status but gates mutations without recent MFA", async () => {
    render(
      <OwnersManager
        currentMembershipId="f52030c1-18af-4e18-a1ec-50997d892ef4"
        mfaRecent={false}
      />,
    );

    expect(await screen.findByText("owner@example.test")).toBeInTheDocument();
    expect(screen.getByText("First Owner")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Set up or verify MFA" }),
    ).toHaveAttribute("href", "/mfa-setup");
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Invite owner" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Revoke" }),
    ).not.toBeInTheDocument();
  });

  it("removes a pending invitation", async () => {
    vi.mocked(operationGet).mockResolvedValue([
      {
        gym_membership_id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
        user_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
        email: "owner@example.test",
        first_name: "First",
        last_name: "Owner",
        phone: "",
        status: "ACTIVE",
        is_owner: true,
        authz_version: 1,
        invited_at: null,
        activated_at: "2026-09-30T00:00:00Z",
      },
      {
        gym_membership_id: "6b1d6a0e-6a4e-4a3e-9c1a-2b7e8f0a1c22",
        user_id: "7c2e7b1f-7b5f-4b4f-8d2b-3c8f9a1b2d33",
        email: "wrong@example.test",
        first_name: "Wrong",
        last_name: "Address",
        phone: "",
        status: "INVITED",
        is_owner: true,
        authz_version: 1,
        invited_at: "2026-10-26T00:00:00Z",
        activated_at: null,
      },
    ]);

    render(
      <OwnersManager
        currentMembershipId="f52030c1-18af-4e18-a1ec-50997d892ef4"
        mfaRecent
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", {
      name: "Remove wrong@example.test?",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Remove invitation" }),
    );

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        "owners/6b1d6a0e-6a4e-4a3e-9c1a-2b7e8f0a1c22/cancel-invitation",
        {},
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Remove wrong@example.test?" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps the remove dialog open and shows the failure", async () => {
    vi.mocked(operationGet).mockResolvedValue([
      {
        gym_membership_id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
        user_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
        email: "owner@example.test",
        first_name: "First",
        last_name: "Owner",
        phone: "",
        status: "ACTIVE",
        is_owner: true,
        authz_version: 1,
        invited_at: null,
        activated_at: "2026-09-30T00:00:00Z",
      },
      {
        gym_membership_id: "6b1d6a0e-6a4e-4a3e-9c1a-2b7e8f0a1c22",
        user_id: "7c2e7b1f-7b5f-4b4f-8d2b-3c8f9a1b2d33",
        email: "wrong@example.test",
        first_name: "Wrong",
        last_name: "Address",
        phone: "",
        status: "INVITED",
        is_owner: true,
        authz_version: 1,
        invited_at: "2026-10-26T00:00:00Z",
        activated_at: null,
      },
    ]);
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Django route is not allowed."),
    );

    render(
      <OwnersManager
        currentMembershipId="f52030c1-18af-4e18-a1ec-50997d892ef4"
        mfaRecent
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", {
      name: "Remove wrong@example.test?",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Remove invitation" }),
    );

    expect(
      await within(dialog).findByRole("alert"),
    ).toHaveTextContent("Django route is not allowed.");
    expect(
      screen.getByRole("dialog", { name: "Remove wrong@example.test?" }),
    ).toBeInTheDocument();
  });

  it("revokes an owner from a confirmation dialog", async () => {
    render(
      <OwnersManager
        currentMembershipId="00000000-0000-4000-8000-000000000001"
        mfaRecent
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    const dialog = screen.getByRole("dialog", {
      name: "Revoke owner@example.test?",
    });
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "No" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke owner" }),
    );
    expect(operationMutation).not.toHaveBeenCalled();
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "A reason of at least 3 characters is required.",
    );

    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "  Left the gym  " },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke owner" }),
    );

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        "owners/f52030c1-18af-4e18-a1ec-50997d892ef4/revoke",
        { reason: "Left the gym" },
      ),
    );
  });
});

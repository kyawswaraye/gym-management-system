import { render, screen } from "@testing-library/react";
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

    expect(
      await screen.findByText("owner@example.test · ACTIVE"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Set up or verify MFA" }),
    ).toHaveAttribute("href", "/mfa-setup");
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Revoke" }),
    ).not.toBeInTheDocument();
  });
});

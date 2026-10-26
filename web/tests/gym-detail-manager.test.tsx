import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  platformGet,
  platformGetCollection,
  platformMutation,
} from "@/features/platform/client";
import { GymDetailManager } from "@/features/platform/gym-detail-manager";
import type { PlatformGymDetail } from "@/types/api";

vi.mock("@/features/platform/client", () => ({
  platformGet: vi.fn(),
  platformGetCollection: vi.fn(),
  platformMutation: vi.fn(),
}));

const getItem = vi.mocked(platformGet);
const getCollection = vi.mocked(platformGetCollection);
const mutate = vi.mocked(platformMutation);

const gym: PlatformGymDetail = {
  id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
  code: "YGN-01",
  name: "Yangon Fitness",
  legal_name: "",
  status: "ACTIVE",
  email: "",
  phone: "",
  address_line1: "",
  address_line2: "",
  city: "Yangon",
  region: "",
  postal_code: "",
  country_code: "MM",
  timezone: "Asia/Yangon",
  currency: "THB",
  subscription_plan_id: "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
  subscription_plan_name: "Trial",
  subscription_status: "ACTIVE",
  subscription_starts_at: null,
  subscription_ends_at: null,
  created_at: "2026-09-30T00:00:00Z",
  updated_at: "2026-09-30T00:00:00Z",
};

describe("Super Admin gym detail manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getItem.mockResolvedValue(gym);
    getCollection.mockResolvedValue({
      success: true,
      data: [
        {
          id: "34485f4c-d7df-4244-bae6-072868cc111a",
          actor_user_id: "f3aa57e5-90ce-48da-afab-1735ee42ace1",
          actor_email: "admin@example.test",
          target_gym_id: gym.id,
          target_gym_code: gym.code,
          target_type: "Gym",
          target_id: gym.id,
          action: "GYM_UPDATED",
          module: "platform",
          outcome: "SUCCESS",
          old_values: {},
          new_values: { name: gym.name },
          request_id: "req_test",
          occurred_at: "2026-09-30T01:00:00Z",
        },
      ],
      meta: { has_more: false, next_after: null },
    });
    mutate.mockResolvedValue(gym);
  });

  it("renders audit but hides unauthorized edit and lifecycle controls", async () => {
    render(
      <GymDetailManager
        gymId={gym.id}
        capabilities={{
          canUpdate: false,
          canManageSubscriptions: false,
          canActivate: false,
          canSuspend: false,
          canViewAudit: true,
          canCreateOwner: false,
          mfaRecent: false,
        }}
      />,
    );

    expect(await screen.findByText("Yangon Fitness")).toBeInTheDocument();
    expect(screen.getByText("GYM_UPDATED")).toBeInTheDocument();
    expect(screen.queryByText("Gym settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Lifecycle")).not.toBeInTheDocument();
    expect(getCollection).toHaveBeenCalledWith(
      `admin/audit-logs?target_gym_id=${gym.id}`,
    );
  });

  it("requires a reason and explicit confirmation before suspension", async () => {
    render(
      <GymDetailManager
        gymId={gym.id}
        capabilities={{
          canUpdate: false,
          canManageSubscriptions: false,
          canActivate: false,
          canSuspend: true,
          canViewAudit: false,
          canCreateOwner: false,
          mfaRecent: false,
        }}
      />,
    );
    await screen.findByText("Yangon Fitness");

    fireEvent.click(screen.getByRole("button", { name: "Change lifecycle" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Lifecycle" })).getByRole(
        "button",
        { name: "Suspend gym" },
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a reason of at least 3 characters.",
    );
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Reason *"), {
      target: { value: "Payment risk" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Suspend gym" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Confirm the lifecycle change before continuing.",
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I confirm this lifecycle change.",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Suspend gym" }));

    await waitFor(() =>
      expect(mutate).toHaveBeenCalledWith(
        `admin/gyms/${gym.id}/suspend`,
        "POST",
        { reason: "Payment risk" },
      ),
    );
  });

  it("explains that a live Owner invitation is not resent", async () => {
    render(
      <GymDetailManager
        gymId={gym.id}
        capabilities={{
          canUpdate: false,
          canManageSubscriptions: false,
          canActivate: false,
          canSuspend: false,
          canViewAudit: false,
          canCreateOwner: true,
          mfaRecent: true,
        }}
      />,
    );
    await screen.findByText("Yangon Fitness");

    expect(
      screen.getByText(
        /A live pending invitation is not resent\. A new link is possible after expiry for a passwordless active Owner\./,
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add Owner" }));
    expect(
      screen.getByText("Required. The setup link is emailed to this address."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.test" },
    });
    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "Owner" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Send Owner invitation" }),
    );

    await waitFor(() =>
      expect(mutate).toHaveBeenCalledWith(
        `admin/gyms/${gym.id}/owners`,
        "POST",
        {
          email: "owner@example.test",
          first_name: "Ada",
          last_name: "Owner",
          phone: "",
          send_invitation: true,
        },
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Owner invitation request accepted. A live pending invitation is not resent. A new link is possible after expiry for a passwordless active Owner.",
    );
    expect(document.body.innerHTML).not.toContain("invitation_token");
    expect(JSON.stringify(mutate.mock.calls)).not.toContain(
      '"send_invitation":false',
    );
  });
});

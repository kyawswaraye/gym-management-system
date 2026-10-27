import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GymsManager } from "@/features/platform/gyms-manager";
import {
  platformGet,
  platformGetCollection,
  provisionGym,
} from "@/features/platform/client";
import type { GymProvisionResult } from "@/types/api";

vi.mock("@/features/platform/client", () => ({
  platformGet: vi.fn(),
  platformGetCollection: vi.fn(),
  provisionGym: vi.fn(),
}));

const getPlans = vi.mocked(platformGet);
const getGyms = vi.mocked(platformGetCollection);
const createGym = vi.mocked(provisionGym);

describe("Super Admin gyms manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getGyms.mockResolvedValue({
      success: true,
      data: [
        {
          id: "gym-1",
          code: "YGN-01",
          name: "Yangon Fitness",
          status: "ACTIVE",
          timezone: "Asia/Yangon",
          currency: "THB",
          subscription_plan_id: "plan-1",
          subscription_plan_name: "Trial",
          subscription_status: "TRIAL",
          created_at: "2026-09-30T00:00:00Z",
        },
      ],
      meta: { has_more: false, next_after: null },
    });
    getPlans.mockResolvedValue([
      {
        id: "plan-1",
        code: "TRIAL",
        name: "Trial",
        price: "0.00",
        currency: "THB",
        billing_interval: "TRIAL",
        status: "ACTIVE",
      },
      {
        id: "plan-retired",
        code: "LEGACY",
        name: "Legacy",
        price: "20.00",
        currency: "THB",
        billing_interval: "MONTHLY",
        status: "RETIRED",
      },
    ]);
  });

  it("shows the gym list but hides provisioning without both permissions", async () => {
    render(<GymsManager canProvision={false} />);

    expect(await screen.findByText("Yangon Fitness")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Provision gym" }),
    ).not.toBeInTheDocument();
    expect(getPlans).not.toHaveBeenCalled();
  });

  it("validates required fields without provisioning", async () => {
    render(<GymsManager canProvision />);
    await screen.findByText("Yangon Fitness");
    expect(getPlans).toHaveBeenCalledWith("admin/subscriptions?status=ACTIVE");
    fireEvent.click(screen.getByRole("button", { name: "Provision gym" }));
    const provision = screen.getByRole("dialog", { name: "Provision gym" });
    expect(
      within(provision).getByRole("option", { name: "Trial (THB)" }),
    ).toBeInTheDocument();
    expect(
      within(provision).queryByRole("option", { name: "Legacy (THB)" }),
    ).not.toBeInTheDocument();
    fireEvent.change(within(provision).getByLabelText("Subscription *"), {
      target: { value: "plan-1" },
    });

    fireEvent.submit(provision);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Complete all required gym and owner fields.",
      ),
    );
    expect(createGym).not.toHaveBeenCalled();
  });

  it("emails the Owner, hides credentials, and retries with the same key", async () => {
    const keys = [
      "11111111-1111-4111-8111-111111111111",
      "22222222-2222-4222-8222-222222222222",
    ] as const;
    let issued = 0;
    vi.spyOn(globalThis.crypto, "randomUUID").mockImplementation(
      () => keys[issued++] ?? keys[1],
    );
    const secretResult = {
      ...provisioned,
      password: "super-secret-password",
      invitation_token: "plain-invite-token",
    };
    createGym
      .mockRejectedValueOnce(new Error("Unable to deliver the Owner email."))
      .mockResolvedValueOnce(secretResult)
      .mockResolvedValueOnce({
        ...provisioned,
        gym: { ...provisioned.gym, code: "YGN-03" },
      });

    render(<GymsManager canProvision />);
    await screen.findByText("Yangon Fitness");
    provisionForm();
    expect(
      screen.getByText(/An Owner setup email is sent to the address below/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Required. The Owner setup link is emailed to this address.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Provision gym and email Owner" }),
    ).toBeEnabled();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('type="password"');

    fillProvisionForm();
    fireEvent.submit(provisionForm());

    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to deliver the Owner email.",
    );
    expect(createGym).toHaveBeenCalledTimes(1);
    expect(createGym.mock.calls[0]?.[0]).toEqual(provisionRequest);
    expect(createGym.mock.calls[0]?.[1]).toBe(keys[0]);
    expect(screen.getByLabelText("Owner email *")).toHaveValue(
      "owner@example.test",
    );
    expect(screen.getByLabelText("Gym name *")).toHaveValue("Second Gym");

    fireEvent.submit(provisionForm());

    expect(await screen.findByRole("status")).toHaveTextContent(
      "YGN-02 was provisioned and an Owner setup email was sent.",
    );
    expect(createGym).toHaveBeenCalledTimes(2);
    expect(createGym.mock.calls[1]?.[0]).toEqual(provisionRequest);
    expect(createGym.mock.calls[1]?.[1]).toBe(keys[0]);
    expect(
      screen.queryByRole("dialog", { name: "Provision gym" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Owner email *")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("super-secret-password");
    expect(document.body.innerHTML).not.toContain("plain-invite-token");
    expect(JSON.stringify(createGym.mock.calls)).not.toContain(
      '"send_invitation":false',
    );

    provisionForm();
    fillProvisionForm();
    fireEvent.submit(provisionForm());

    await waitFor(() => expect(createGym).toHaveBeenCalledTimes(3));
    expect(createGym.mock.calls[2]?.[1]).toBe(keys[1]);
    expect(createGym.mock.calls[2]?.[0].owner.send_invitation).toBe(true);
  });
});

const provisioned = {
  gym: {
    id: "gym-2",
    code: "YGN-02",
    name: "Second Gym",
    legal_name: "",
    status: "ACTIVE",
    email: "",
    phone: "",
    address_line1: "",
    address_line2: "",
    city: "",
    region: "",
    postal_code: "",
    country_code: "",
    timezone: "Asia/Yangon",
    currency: "THB",
    subscription_plan_id: "plan-1",
    subscription_status: "TRIAL",
    subscription_starts_at: null,
    subscription_ends_at: null,
  },
  owner: {
    user: {
      id: "user-1",
      email: "owner@example.test",
      first_name: "Ada",
      last_name: "Owner",
      phone: "",
    },
    gym_membership: {
      id: "membership-1",
      status: "ACTIVE",
      is_owner: true,
    },
  },
  roles: [],
  feature_entitlements: [],
  expense_categories: [],
} satisfies GymProvisionResult;

const provisionRequest = {
  gym: {
    name: "Second Gym",
    legal_name: "Second Gym",
    email: "",
    phone: "",
    address_line1: "",
    city: "",
    region: "",
    country_code: "",
    timezone: "Asia/Yangon",
    currency: "THB",
    subscription_plan_id: "plan-1",
    subscription_status: "TRIAL",
  },
  owner: {
    email: "owner@example.test",
    first_name: "Ada",
    last_name: "Owner",
    phone: "",
    send_invitation: true,
  },
};

function provisionForm() {
  if (!screen.queryByRole("dialog", { name: "Provision gym" })) {
    fireEvent.click(screen.getByRole("button", { name: "Provision gym" }));
  }
  const form = screen.getByRole("dialog", { name: "Provision gym" });
  if (!(form instanceof HTMLFormElement)) {
    throw new Error("Provision form was not rendered.");
  }
  return form;
}

function fillProvisionForm() {
  fireEvent.change(screen.getByLabelText("Gym name *"), {
    target: { value: "Second Gym" },
  });
  fireEvent.change(screen.getByLabelText("Subscription *"), {
    target: { value: "plan-1" },
  });
  fireEvent.change(screen.getByLabelText("Owner first name *"), {
    target: { value: "Ada" },
  });
  fireEvent.change(screen.getByLabelText("Owner last name *"), {
    target: { value: "Owner" },
  });
  fireEvent.change(screen.getByLabelText("Owner email *"), {
    target: { value: "owner@example.test" },
  });
}

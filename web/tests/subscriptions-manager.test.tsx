import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { platformGet, platformMutation } from "@/features/platform/client";
import { SubscriptionsManager } from "@/features/platform/subscriptions-manager";
import type { PlatformSubscription } from "@/types/api";

vi.mock("@/features/platform/client", () => ({
  platformGet: vi.fn(),
  platformMutation: vi.fn(),
}));

const getPlans = vi.mocked(platformGet);
const updatePlan = vi.mocked(platformMutation);

const active: PlatformSubscription = {
  id: "39F07D2B-BBD8-43D2-9C18-E4CBF3AF131E",
  code: "GROWTH",
  name: "Growth",
  status: "ACTIVE",
  price: "10.00",
  currency: "THB",
  billing_interval: "MONTHLY",
};

const retired: PlatformSubscription = {
  id: "a1b2c3d4-e5f6-4789-a012-3456789abcde",
  code: "LEGACY",
  name: "Legacy",
  status: "RETIRED",
  price: "0.00",
  currency: "THB",
  billing_interval: "TRIAL",
};

describe("subscription plan manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPlans.mockResolvedValue([active, retired]);
  });

  it("does not load plans without permission", async () => {
    render(<SubscriptionsManager canManage={false} />);

    expect(
      screen.getByText("You do not have access to subscription plans."),
    ).toBeInTheDocument();
    await waitFor(() => expect(getPlans).not.toHaveBeenCalled());
  });

  it("shows active and retired plans and patches only strict fields", async () => {
    updatePlan.mockResolvedValue({
      ...active,
      id: active.id.toLowerCase(),
      name: "Growth Plus",
      created_at: "2026-09-30T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    });
    render(<SubscriptionsManager canManage />);

    expect(
      await screen.findByRole("article", { name: "GROWTH plan" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "LEGACY plan" }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Current status for GROWTH"),
    ).toHaveTextContent("ACTIVE");
    expect(
      screen.getByLabelText("Current status for LEGACY"),
    ).toHaveTextContent("RETIRED");
    expect(getPlans).toHaveBeenCalledWith("admin/subscriptions?status=ALL");
    expect(
      screen.queryByText(/feature|usage|entitlement/i),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit GROWTH" }));
    fireEvent.change(screen.getByLabelText("Name for GROWTH"), {
      target: { value: "Growth Plus" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save GROWTH" }));

    await waitFor(() =>
      expect(updatePlan).toHaveBeenCalledWith(
        `admin/subscriptions/${active.id.toLowerCase()}`,
        "PATCH",
        { name: "Growth Plus" },
      ),
    );
    expect(updatePlan.mock.calls[0]?.[2]).not.toHaveProperty("code");
    expect(updatePlan.mock.calls[0]?.[2]).not.toHaveProperty("currency");
    expect(updatePlan.mock.calls[0]?.[2]).not.toHaveProperty("id");
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Edit GROWTH" }),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit GROWTH" }));
    expect(screen.getByLabelText("Name for GROWTH")).toHaveValue("Growth Plus");
  });

  it("keeps the catalog status when retirement is rejected", async () => {
    updatePlan.mockRejectedValue(
      new Error("This subscription plan is still in use."),
    );
    render(<SubscriptionsManager canManage />);
    await screen.findByLabelText("Current status for GROWTH");
    fireEvent.click(screen.getByRole("button", { name: "Edit GROWTH" }));

    fireEvent.change(screen.getByLabelText("Status for GROWTH"), {
      target: { value: "RETIRED" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save GROWTH" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This subscription plan is still in use.",
    );
    expect(
      screen.getByLabelText("Current status for GROWTH"),
    ).toHaveTextContent("ACTIVE");
    expect(
      screen.getByLabelText("Current status for LEGACY"),
    ).toHaveTextContent("RETIRED");
  });

  it("rejects an invalid price or lowercase interval before saving", async () => {
    render(<SubscriptionsManager canManage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit GROWTH" }));

    fireEvent.change(screen.getByLabelText("Price for GROWTH"), {
      target: { value: "-1.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save GROWTH" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a nonnegative price with up to 2 decimal places.",
    );

    fireEvent.change(screen.getByLabelText("Price for GROWTH"), {
      target: { value: "10.00" },
    });
    fireEvent.change(screen.getByLabelText("Billing interval for GROWTH"), {
      target: { value: "monthly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save GROWTH" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter an uppercase billing interval.",
    );
    expect(updatePlan).not.toHaveBeenCalled();
  });

  it("shows an empty catalog and a load error", async () => {
    getPlans.mockResolvedValueOnce([]);
    const { rerender } = render(<SubscriptionsManager canManage />);
    expect(
      await screen.findByText("No subscription plans found."),
    ).toBeInTheDocument();

    getPlans.mockRejectedValueOnce(new Error("Plans unavailable."));
    rerender(<SubscriptionsManager canManage={false} />);
    rerender(<SubscriptionsManager canManage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Plans unavailable.",
    );
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GymSelectionForm } from "@/features/auth/gym-selection-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

describe("gym selection form", () => {
  it("renders only safe membership labels from server-provided choices", () => {
    render(
      <GymSelectionForm
        gyms={[
          {
            gym_membership_id: "m1",
            gym: { code: "A", name: "Alpha Gym" },
          },
          {
            gym_membership_id: "m2",
            gym: { code: "B", name: "Beta Gym" },
          },
        ]}
      />,
    );

    expect(screen.getByText("Alpha Gym")).toBeInTheDocument();
    expect(screen.getByText("Beta Gym")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Alpha Gym" })).toHaveAttribute(
      "value",
      "m1",
    );
  });

  it("requires a gym choice before calling the network", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(
      <GymSelectionForm
        gyms={[{ gym_membership_id: "m1", gym: { name: "Alpha Gym" } }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Choose a gym.");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/features/auth/login-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

describe("login form", () => {
  it("exposes accessible fields and an expired-session state", () => {
    render(<LoginForm sessionExpired />);

    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("session expired");
  });

  it("validates required credentials without a network request", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<LoginForm />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter both your email and password.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

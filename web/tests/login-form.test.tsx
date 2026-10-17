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

  it("switches to the authenticator challenge without exposing its token", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ data: { csrf_token: "csrf" } }))
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            requires_mfa: true,
            mfa_challenge_expires_at: "2026-10-01T00:00:00Z",
          },
        }),
      );
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByLabelText("Authenticator code"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("mfa_challenge_token");
  });
});

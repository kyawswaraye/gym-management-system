import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ForgotPasswordPage from "@/app/(auth)/forgot-password/page";
import { ForgotPasswordForm } from "@/features/auth/forgot-password-form";
import { LoginForm } from "@/features/auth/login-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

describe("forgot password", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("exposes an accessible forgot page and a login link", () => {
    render(<ForgotPasswordPage />);
    expect(
      screen.getByRole("heading", { name: "Forgot password" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(
      screen.getByRole("link", { name: "Back to sign in" }),
    ).toHaveAttribute("href", "/login");
  });

  it("links to forgot password from sign in", () => {
    render(<LoginForm />);
    expect(
      screen.getByRole("link", { name: "Forgot password" }),
    ).toHaveAttribute("href", "/forgot-password");
  });

  it("shows the constant confirmation after a successful request", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ data: { csrf_token: "csrf" } }))
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: null,
          message:
            "If an account exists for that email, a reset link will be sent.",
        }),
      );
    render(<ForgotPasswordForm />);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "staff@example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "If an account exists for that email, a reset link will be sent.",
    );
    expect(document.body.textContent).not.toContain(
      "staff@example.test exists",
    );
  });
});

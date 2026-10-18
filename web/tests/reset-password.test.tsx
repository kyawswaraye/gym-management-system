import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ResetPasswordPage from "@/app/(auth)/reset-password/page";
import { ResetPasswordForm } from "@/features/auth/reset-password-form";

const { refresh, replace } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

const token = "reset-token-value";

describe("reset password", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("reports an incomplete reset link", async () => {
    render(
      await ResetPasswordPage({
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This reset link is incomplete.",
    );
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
  });

  it("keeps the URL token out of the page and browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    render(
      await ResetPasswordPage({
        searchParams: Promise.resolve({ token }),
      }),
    );
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(token);
    expect(setItem).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain(token);
  });

  it("does not submit until the passwords match", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<ResetPasswordForm token={token} />);
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "different horse battery staple" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Passwords do not match.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the token once and returns to sign in", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json({ data: { csrf_token: "csrf-token" } }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    render(<ResetPasswordForm token={token} />);
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "replaced horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "replaced horse battery staple" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));
    await vi.waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/password/reset",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          reset_token: token,
          password: "replaced horse battery staple",
          password_confirmation: "replaced horse battery staple",
        }),
      }),
    );
    expect(document.cookie).not.toContain(token);
  });
});

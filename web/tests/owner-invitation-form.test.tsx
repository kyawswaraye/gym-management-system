import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AcceptOwnerInvitationPage from "@/app/(auth)/accept-owner-invitation/page";
import { OwnerInvitationForm } from "@/features/auth/owner-invitation-form";

const { refresh, replace } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

const token = "owner-invite-token";

describe("owner invitation acceptance", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("reports an incomplete invitation link", async () => {
    render(
      await AcceptOwnerInvitationPage({
        searchParams: Promise.resolve({}),
      }),
    );

    expect(
      screen.getByText("This invitation link is incomplete."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("keeps the URL token out of the page and browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    render(
      await AcceptOwnerInvitationPage({
        searchParams: Promise.resolve({ token }),
      }),
    );

    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(token);
    expect(setItem).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain(token);
  });

  it("posts same-origin without a bearer credential", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json({ data: { csrf_token: "csrf-token" } }),
      )
      .mockResolvedValueOnce(Response.json({ success: true, data: {} }))
      .mockResolvedValueOnce(
        Response.json({
          success: true,
          data: {
            principal_type: "STAFF",
            permissions: ["gym.view"],
            features: [],
            mfa_enabled: false,
            mfa_recent: false,
            user: { email: "owner@example.test" },
            gym: { id: "gym-1" },
            gym_membership: { id: "membership-1", is_owner: true },
          },
        }),
      );
    render(<OwnerInvitationForm token={token} />);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Accept Owner invitation" }),
    );

    await waitFor(() => expect(replace).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/auth/accept-owner-invitation");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers(init.headers).has("authorization")).toBe(false);
    expect(JSON.stringify(init.headers)).not.toMatch(
      /access_token|refresh_token|Bearer/,
    );
    expect(JSON.parse(String(init.body))).toEqual({
      invitation_token: token,
      password: "correct horse battery staple",
      password_confirmation: "correct horse battery staple",
    });
    expect(document.body.innerHTML).not.toContain(token);
  });

  it("shows the password policy error instead of the generic request message", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        Response.json({ data: { csrf_token: "csrf-token" } }),
      )
      .mockResolvedValueOnce(
        Response.json(
          {
            success: false,
            error: {
              message: "The request could not be processed.",
              fields: {
                non_field_errors: ["This password is too common."],
              },
            },
          },
          { status: 400 },
        ),
      );
    render(<OwnerInvitationForm token={token} />);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "Password1234xx" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "Password1234xx" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Accept Owner invitation" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This password is too common.",
    );
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(replace).not.toHaveBeenCalled();
    expect(document.body.innerHTML).not.toContain(token);
  });
});

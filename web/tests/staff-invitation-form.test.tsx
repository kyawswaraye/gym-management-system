import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AcceptStaffInvitationPage from "@/app/(auth)/accept-staff-invitation/page";
import { StaffInvitationForm } from "@/features/auth/staff-invitation-form";

const { refresh, replace } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

const token = "staff-invite-token";

describe("staff invitation acceptance", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("reports an incomplete invitation link", async () => {
    render(
      await AcceptStaffInvitationPage({
        searchParams: Promise.resolve({}),
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This invitation link is incomplete.",
    );
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("keeps the URL token out of the page and browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    render(
      await AcceptStaffInvitationPage({
        searchParams: Promise.resolve({ token }),
      }),
    );

    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("First name")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(token);
    expect(setItem).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain(token);
  });

  it("does not submit until the passwords match", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<StaffInvitationForm token={token} />);

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "different horse battery staple" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Accept staff invitation" }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Passwords do not match.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the token once and opens the authenticated home", async () => {
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
            permissions: ["staff.view"],
            features: [],
            mfa_enabled: false,
            mfa_recent: false,
            user: { email: "ada@example.test" },
            gym: { id: "gym-1" },
            gym_membership: { id: "membership-1", is_owner: false },
          },
        }),
      );
    render(<StaffInvitationForm token={token} />);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Ada Trainer" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Accept staff invitation" }),
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/staff"));
    expect(refresh).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [, acceptInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/auth/accept-staff-invitation",
    );
    expect(acceptInit.credentials).toBe("same-origin");
    expect(new Headers(acceptInit.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
    expect(new Headers(acceptInit.headers).has("authorization")).toBe(false);
    expect(JSON.parse(String(acceptInit.body))).toEqual({
      invitation_token: token,
      password: "correct horse battery staple",
      password_confirmation: "correct horse battery staple",
      first_name: "Ada",
      display_name: "Ada Trainer",
    });
  });

  it("shows a password field error from the BFF", async () => {
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
              fields: { password: ["This password is too common."] },
            },
          },
          { status: 400 },
        ),
      );
    render(<StaffInvitationForm token={token} />);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Accept staff invitation" }),
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

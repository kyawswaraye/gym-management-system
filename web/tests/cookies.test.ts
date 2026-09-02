import { describe, expect, it } from "vitest";

import {
  AUTH_COOKIES,
  SELECTION_COOKIE_MAX_AGE,
  authCookieOptions,
  clearCookieOptions,
  csrfCookieOptions,
} from "@/lib/auth/cookies";

describe("authentication cookie policy", () => {
  it("sets host-scoped secure HttpOnly auth cookies", () => {
    const options = authCookieOptions(true, 600);
    expect(options).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      secure: true,
      path: "/",
      maxAge: 600,
    });
    expect(options).not.toHaveProperty("domain");
  });

  it("keeps only the CSRF token readable", () => {
    expect(csrfCookieOptions(true)).toMatchObject({
      httpOnly: false,
      sameSite: "lax",
      secure: true,
      path: "/",
    });
  });

  it("keeps selection cookies host-scoped and five minutes or less", () => {
    expect(AUTH_COOKIES.selectionTicket).toBe("gms_selection_ticket");
    expect(SELECTION_COOKIE_MAX_AGE).toBe(300);
    expect(authCookieOptions(true, SELECTION_COOKIE_MAX_AGE)).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      secure: true,
      path: "/",
      maxAge: 300,
    });
  });

  it("clears with matching security and path attributes", () => {
    expect(clearCookieOptions(true, true)).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      secure: true,
      path: "/",
      maxAge: 0,
    });
  });
});

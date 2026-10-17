import type { ResponseCookie } from "next/dist/compiled/@edge-runtime/cookies";

export const AUTH_COOKIES = {
  access: "gms_access",
  refresh: "gms_refresh",
  csrf: "gms_csrf",
  selectionTicket: "gms_selection_ticket",
  selectionChoices: "gms_selection_choices",
  mfaChallenge: "gms_mfa_challenge",
} as const;

const COOKIE_PATH = "/";
export const SELECTION_COOKIE_MAX_AGE = 300;

export function authCookieOptions(
  secure: boolean,
  maxAge?: number,
): Partial<ResponseCookie> {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: COOKIE_PATH,
    ...(maxAge === undefined ? {} : { maxAge }),
  };
}

export function csrfCookieOptions(secure: boolean): Partial<ResponseCookie> {
  return {
    httpOnly: false,
    sameSite: "lax",
    secure,
    path: COOKIE_PATH,
  };
}

export function clearCookieOptions(
  secure: boolean,
  httpOnly: boolean,
): Partial<ResponseCookie> {
  return {
    httpOnly,
    sameSite: "lax",
    secure,
    path: COOKIE_PATH,
    expires: new Date(0),
    maxAge: 0,
  };
}

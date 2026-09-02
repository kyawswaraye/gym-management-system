import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { serverAuthMe } from "@/lib/api/server";
import { AUTH_COOKIES } from "@/lib/auth/cookies";
import type { ApiEnvelope, CurrentUser } from "@/types/api";

async function readCurrentUser(): Promise<CurrentUser | null> {
  const token = (await cookies()).get(AUTH_COOKIES.access)?.value;
  if (!token) return null;

  const response = await serverAuthMe(token);
  if (response.status === 401 || response.status === 403) return null;
  if (!response.ok)
    throw new Error("Unable to load the authenticated session.");
  const body = (await response.json()) as ApiEnvelope<CurrentUser>;
  return body.data;
}

export async function requireTenantSession(): Promise<CurrentUser> {
  const session = await readCurrentUser();
  if (!session) redirect("/login?reason=expired");
  if (!session.gym || !session.gym_membership) redirect("/select-gym");
  return session;
}

export async function requirePlatformSession(): Promise<CurrentUser> {
  const session = await readCurrentUser();
  if (!session) redirect("/login?reason=expired");
  if (!session.permissions.some((code) => code.startsWith("platform."))) {
    redirect("/dashboard");
  }
  return session;
}

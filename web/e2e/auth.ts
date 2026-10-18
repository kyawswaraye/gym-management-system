import { expect, type Page } from "@playwright/test";

import { totpCode } from "./totp";

export type E2EAccount = {
  email: string;
  password: string;
  totpSecret: string;
};

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim() ?? "";
  if (!value) {
    throw new Error(`${name} is required for browser E2E.`);
  }
  return value;
}

export const platformAccount: E2EAccount = {
  email: "e2e-platform@example.test",
  password: requiredEnv("E2E_PLATFORM_PASSWORD"),
  totpSecret: requiredEnv("E2E_PLATFORM_TOTP_SECRET"),
};

export const ownerAccount: E2EAccount = {
  email: "e2e-owner@example.test",
  password: requiredEnv("E2E_OWNER_PASSWORD"),
  totpSecret: requiredEnv("E2E_OWNER_TOTP_SECRET"),
};

export const managerAccount: E2EAccount = {
  email: "e2e-manager@example.test",
  password: requiredEnv("E2E_MANAGER_PASSWORD"),
  totpSecret: requiredEnv("E2E_MANAGER_TOTP_SECRET"),
};

export function memberAccount(): E2EAccount {
  return {
    email: "e2e-member@example.test",
    password: requiredEnv("E2E_MEMBER_PASSWORD"),
    totpSecret: "",
  };
}

const SECRET_RESPONSE =
  /"(?:access_token|refresh_token|mfa_challenge_token|invitation_token|gym_selection_ticket)"/;

const STORAGE_MARKERS = [
  "access_token",
  "refresh_token",
  "mfa_challenge",
  "gms_access",
  "gms_refresh",
  "gms_mfa",
  "gms_selection",
  "invitation_token",
  "gym_selection",
];

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function responsePath(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}

function responseBody(response: {
  text: () => Promise<string>;
}): Promise<string> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(""), 5_000);
    void response.text().then(
      (body) => {
        clearTimeout(timer);
        resolve(body);
      },
      () => {
        clearTimeout(timer);
        resolve("");
      },
    );
  });
}

export function watchJsonResponses(
  page: Page,
  forbidden: readonly string[] = [],
): () => Promise<void> {
  const pending: Promise<void>[] = [];
  const leaks: string[] = [];
  const serverErrors: string[] = [];
  const needles = forbidden
    .map((secret) => secret.trim().toLowerCase())
    .filter((secret) => secret.length >= 8);
  page.on("response", (response) => {
    const method = response.request().method();
    const pathname = responsePath(response.url());
    if (response.status() >= 500 && MUTATING_METHODS.has(method)) {
      serverErrors.push(`${method} ${pathname} ${response.status()}`);
    }
    const contentType = response.headers()["content-type"] ?? "";
    if (!contentType.includes("json")) return;
    pending.push(
      responseBody(response).then((body) => {
        if (!body) return;
        if (SECRET_RESPONSE.test(body)) leaks.push(pathname);
        const lowered = body.toLowerCase();
        if (needles.some((needle) => lowered.includes(needle))) {
          leaks.push(pathname);
        }
      }),
    );
  });
  return async () => {
    await Promise.all(pending);
    expect(serverErrors).toEqual([]);
    expect(leaks).toEqual([]);
  };
}

async function submitAuthenticatorCode(
  page: Page,
  secret: string,
): Promise<void> {
  await page
    .getByLabel("Authenticator code", { exact: true })
    .fill(totpCode(secret));
  await page.getByRole("button", { name: "Verify code", exact: true }).click();
}

export async function signIn(page: Page, account: E2EAccount): Promise<void> {
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByLabel("Authenticator code", { exact: true }),
  ).toBeVisible();
  await submitAuthenticatorCode(page, account.totpSecret);
  try {
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
  } catch (error) {
    const alert = page.locator("p.error[role='alert']");
    if (!(await alert.isVisible())) throw error;
    const message = (await alert.innerText()).trim();
    if (/internal error/i.test(message)) throw error;
    const nextStepAt = (Math.floor(Date.now() / 30_000) + 1) * 30_000;
    await expect
      .poll(() => Date.now(), { timeout: 35_000 })
      .toBeGreaterThan(nextStepAt);
    await submitAuthenticatorCode(page, account.totpSecret);
    await expect(page).not.toHaveURL(/\/login/);
  }
}

export async function expectNoBrowserSecrets(
  page: Page,
  secrets: readonly string[],
): Promise<void> {
  const storage = await page.evaluate(() => ({
    localStorage: Object.entries(localStorage),
    sessionStorage: Object.entries(sessionStorage),
    documentCookie: document.cookie,
  }));
  const serialized = JSON.stringify(storage).toLowerCase();
  for (const marker of STORAGE_MARKERS) {
    expect(serialized).not.toContain(marker);
  }
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const secret of secrets) {
    const needle = secret.toLowerCase();
    expect(serialized).not.toContain(needle);
    expect(text).not.toContain(needle);
  }
  const cookies = await page.context().cookies();
  expect(cookies.find((cookie) => cookie.name === "gms_access")?.httpOnly).toBe(
    true,
  );
  expect(
    cookies.find((cookie) => cookie.name === "gms_refresh")?.httpOnly,
  ).toBe(true);
  expect(
    cookies.find((cookie) => cookie.name === "gms_mfa_challenge"),
  ).toBeUndefined();
  expect(
    cookies.find((cookie) => cookie.name === "gms_selection_ticket"),
  ).toBeUndefined();
}

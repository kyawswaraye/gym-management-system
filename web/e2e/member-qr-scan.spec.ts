import { expect, test, type Page } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  memberAccount,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

const API_URL =
  process.env.DJANGO_API_URL ??
  `http://127.0.0.1:${process.env.E2E_API_PORT ?? "8010"}`;

test("a linked member redeems an onsite QR without storing member tokens", async ({
  page,
  playwright,
}) => {
  const member = memberAccount();
  const assertResponses = watchJsonResponses(page, [member.password]);
  const browserCalls: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (
      url.includes("/member-auth/") ||
      url.includes("/member/check-ins/scan")
    ) {
      browserCalls.push(url);
    }
  });
  await signIn(page, ownerAccount);

  const issued = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/operations\/check-in-challenges\/?$/.test(
        new URL(response.url()).pathname,
      ),
  );
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Check-ins", exact: true })
    .click();
  await page.getByRole("tab", { name: "QR display", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Onsite check-in code", exact: true }),
  ).toBeVisible();
  const challengeResponse = await issued;
  const challengeBody = await challengeResponse.text();
  expect(challengeResponse.status(), challengeBody).toBe(201);
  const payload = challengePayload(challengeBody);
  await expectChallengeStaysOnScreen(page, payload);

  const mobile = await playwright.request.newContext({ baseURL: API_URL });
  try {
    const login = await mobile.post("/api/v1/member-auth/login/", {
      data: { email: member.email, password: member.password },
    });
    const loginBody = await login.text();
    expect(login.ok(), loginBody).toBeTruthy();
    const session = responseData(JSON.parse(loginBody));
    const access = stringField(session, "access_token");
    const refresh = stringField(session, "refresh_token");

    const scan = await mobile.post("/api/v1/member/check-ins/scan/", {
      headers: { Authorization: `Bearer ${access}` },
      data: { payload },
    });
    const scanBody = await scan.text();
    expect(scan.status(), scanBody).toBe(201);
    const visit = responseData(JSON.parse(scanBody));
    expect(visit.method).toBe("QR");
    expect(visit.status).toBe("OPEN");

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Check-ins", exact: true })
      .click();
    const openVisits = page.getByRole("region", {
      name: "Currently checked in",
    });
    await expect(openVisits).toContainText(
      /E2Escan Visitor \(MEM-\d+\) · Open/,
    );
    await expect(openVisits).toContainText("E2E Scan Plan");

    await page
      .getByLabel("Search active members", { exact: true })
      .fill("E2Escan");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const selectMember = page.getByRole("button", {
      name: /Select E2Escan Visitor \(MEM-\d+\)/,
    });
    await expect(selectMember).toBeVisible();
    const memberLabel =
      (await selectMember.innerText()).replace(/^Select /, "") || "";
    await selectMember.click();
    const recent = page.getByRole("region", { name: memberLabel });
    await expect(recent).toContainText(`${memberLabel} · Open`);
    await expect(recent).toContainText("E2E Scan Plan");

    const membersNav = page.getByRole("navigation", { name: "Primary" });
    await membersNav
      .getByRole("button", { name: "Members", exact: true })
      .click();
    await membersNav
      .getByRole("link", { name: "Members", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Members", exact: true }),
    ).toBeVisible();
    const memberSearch = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === "/api/operations/members" &&
        new URL(response.url()).searchParams.get("search") === "E2Escan",
    );
    await page.getByLabel("Search members", { exact: true }).fill("E2Escan");
    expect((await memberSearch).ok()).toBeTruthy();
    await page
      .getByRole("link", { name: "E2Escan Visitor", exact: true })
      .click();
    const summary = page.getByRole("region", { name: "Check-ins" });
    await expect(summary).toContainText("Checked in");

    const logout = await mobile.post("/api/v1/member-auth/logout/", {
      data: { refresh_token: refresh },
    });
    expect(logout.status()).toBe(204);
    const replay = await mobile.post("/api/v1/member-auth/refresh/", {
      data: { refresh_token: refresh },
    });
    expect(replay.status()).toBe(401);

    await expectNoMemberSessionLeak(page, [
      access,
      refresh,
      payload,
      member.password,
    ]);
    const jar = await mobile.storageState();
    const stored = JSON.stringify(jar);
    expect(stored).not.toContain(access);
    expect(stored).not.toContain(refresh);
    expect(stored).not.toContain(payload);
  } finally {
    await mobile.dispose();
  }
  expect(browserCalls).toEqual([]);
  await assertResponses();
});

function challengePayload(body: string): string {
  const data = responseData(JSON.parse(body));
  const payload = data.payload;
  if (typeof payload !== "string" || !payload.startsWith("gms-checkin:v1:")) {
    throw new Error(
      "The check-in challenge response omitted its one-time code.",
    );
  }
  return payload;
}

function responseData(payload: unknown): Record<string, unknown> {
  if (typeof payload !== "object" || payload === null || !("data" in payload)) {
    throw new Error("The API response did not include a data object.");
  }
  const data = payload.data;
  if (typeof data !== "object" || data === null) {
    throw new Error("The API response data was empty.");
  }
  return data as Record<string, unknown>;
}

function stringField(data: Record<string, unknown>, key: string): string {
  const value = data[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`The member session omitted ${key}.`);
  }
  return value;
}

async function expectChallengeStaysOnScreen(
  page: Page,
  payload: string,
): Promise<void> {
  const image = page.getByRole("img", {
    name: "Current onsite check-in code",
    exact: true,
  });
  await expect(image).toBeVisible();
  const src = (await image.getAttribute("src")) ?? "";
  const text = await page.locator("body").innerText();
  expect(src.startsWith("data:image/")).toBe(true);
  expect(src.includes(payload)).toBe(false);
  expect(text.includes(payload)).toBe(false);
  expect(text.includes("gms-checkin:v1:")).toBe(false);
}

async function expectNoMemberSessionLeak(
  page: Page,
  secrets: string[],
): Promise<void> {
  const storage = await page.evaluate(() => ({
    local: Object.entries(window.localStorage),
    session: Object.entries(window.sessionStorage),
    cookie: document.cookie,
  }));
  const rendered = JSON.stringify(storage);
  for (const secret of secrets) {
    expect(rendered.includes(secret)).toBe(false);
  }
  const cookies = await page.context().cookies();
  const cookieText = cookies.map((cookie) => cookie.value).join("\n");
  for (const secret of secrets) {
    expect(cookieText.includes(secret)).toBe(false);
  }
  await expectNoBrowserSecrets(page, secrets);
}

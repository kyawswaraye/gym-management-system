import {
  expect,
  type APIRequestContext,
  type Page,
  test,
} from "@playwright/test";

import { memberAccount, ownerAccount, receptionAccount, signIn } from "./auth";

test.describe.configure({ mode: "serial" });

const TITLE = "Browser notice 1112";
const EDITED = "Browser notice updated 1112";
const BODY = "The front desk opens at eight.";
const FORGED_GYM = "00000000-0000-0000-0000-000000000099";
const API_URL = process.env.DJANGO_API_URL ?? "http://127.0.0.1:8010";

async function postJson(
  page: Page,
  path: string,
  data: Record<string, string>,
): Promise<{ status: number; body: string }> {
  return page.evaluate(
    async ({ path: target, data: payload }) => {
      const csrfResponse = await fetch("/api/auth/csrf", {
        credentials: "same-origin",
      });
      const csrf = (await csrfResponse.json()) as {
        data?: { csrf_token?: string };
      };
      const response = await fetch(target, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf.data?.csrf_token ?? "",
        },
        body: JSON.stringify(payload),
      });
      return { status: response.status, body: await response.text() };
    },
    { path, data },
  );
}

async function memberInbox(api: APIRequestContext): Promise<string> {
  const member = memberAccount();
  const login = await api.post("/api/v1/member-auth/login/", {
    data: { email: member.email, password: member.password },
  });
  expect(login.ok(), await login.text()).toBeTruthy();
  const session = (await login.json()) as {
    data?: { access_token?: string };
  };
  const access = session.data?.access_token ?? "";
  const inbox = await api.get("/api/v1/member/notifications/", {
    headers: { Authorization: `Bearer ${access}` },
  });
  expect(inbox.ok(), await inbox.text()).toBeTruthy();
  return inbox.text();
}

test("authorized staff manage an announcement and the member inbox follows", async ({
  page,
  browser,
  playwright,
}) => {
  const api = await playwright.request.newContext({ baseURL: API_URL });
  await signIn(page, ownerAccount);
  await page.goto("/announcements");
  await expect(
    page.getByRole("heading", { name: "Announcements", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "New announcement" }),
  ).toBeVisible();

  await page.getByLabel("Announcement title", { exact: true }).fill(TITLE);
  await page.getByLabel("Announcement body", { exact: true }).fill(BODY);
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  const draft = page.locator("li").filter({ hasText: TITLE });
  await expect(draft).toContainText("DRAFT");

  await draft.getByRole("button", { name: "Edit draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Edit draft" })).toBeVisible();
  await page.getByLabel("Announcement title", { exact: true }).fill(EDITED);
  await page.getByRole("button", { name: "Update draft", exact: true }).click();
  const edited = page.locator("li").filter({ hasText: EDITED });
  await expect(edited).toContainText("DRAFT");
  await expect(page.locator("li").filter({ hasText: TITLE })).toHaveCount(0);

  await edited.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(edited).toContainText("PUBLISHED");
  await expect.poll(async () => memberInbox(api)).toContain(EDITED);

  const receptionist = await browser.newPage();
  await signIn(receptionist, receptionAccount);
  await receptionist.goto("/announcements");
  await expect(
    receptionist.getByRole("heading", { name: "Announcements", exact: true }),
  ).toBeVisible();
  await expect(receptionist.getByText(EDITED)).toBeVisible();
  await expect(
    receptionist.getByRole("button", { name: "Save draft", exact: true }),
  ).toHaveCount(0);
  await expect(
    receptionist.getByRole("button", { name: "Publish", exact: true }),
  ).toHaveCount(0);
  const denied = await postJson(
    receptionist,
    "/api/operations/announcements/",
    {
      type: "GENERAL",
      title: "Reception draft 1112",
      body: "This must not be stored.",
      audience: "ALL_ACTIVE_MEMBERS",
    },
  );
  expect(denied.status).toBe(403);
  expect(denied.body).not.toContain("CSRF_FAILED");

  const forged = await postJson(page, "/api/operations/announcements/", {
    gym_id: FORGED_GYM,
    type: "GENERAL",
    title: "Foreign gym notice 1112",
    body: "This must stay out of another gym.",
    audience: "ALL_ACTIVE_MEMBERS",
  });
  expect(forged.status).toBe(400);
  expect(forged.body).not.toContain(FORGED_GYM);
  await expect(page.getByText("Foreign gym notice 1112")).toHaveCount(0);
  expect(await memberInbox(api)).not.toContain(FORGED_GYM);

  await edited.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(edited).toContainText("CANCELLED");
  await expect.poll(async () => memberInbox(api)).not.toContain(EDITED);
  await receptionist.context().close();
  await api.dispose();
});

test("a mocked assistant keeps a Burmese conversation across refresh and a provider failure", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await signIn(page, ownerAccount);
  await page.goto("/assistant");
  await expect(
    page.locator("header").getByText("Assistant", { exact: true }),
  ).toBeVisible();

  const log = page.getByRole("log");
  await page.locator("#assistant-message").fill("အဖွဲ့ဝင် စာရင်း ရှာ");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(log).toContainText("အဖွဲ့ဝင် စာရင်း ရှာ");
  await expect(page.locator("#assistant-message")).toBeEnabled({
    timeout: 30_000,
  });
  await expect(log).not.toContainText("workflow-state v1");
  await expect(log).not.toContainText("Invented Person");

  await page.locator("#assistant-message").fill("ငွေပေးချေမှု စာရင်း ရှာ");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(log).toContainText("ငွေပေးချေမှု စာရင်း ရှာ");
  await expect(log).toContainText("အဖွဲ့ဝင် စာရင်း ရှာ");
  await expect(page.locator("#assistant-message")).toBeEnabled({
    timeout: 30_000,
  });

  await page.reload();
  await expect(page.getByRole("log")).toContainText("အဖွဲ့ဝင် စာရင်း ရှာ");
  await expect(page.getByRole("log")).toContainText("ငွေပေးချေမှု စာရင်း ရှာ");
  await expect(page.getByRole("log")).not.toContainText("workflow-state v1");

  await page.getByRole("button", { name: "History", exact: true }).click();
  await page
    .locator("#assistant-history")
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await expect(page.getByText("အဖွဲ့ဝင် စာရင်း ရှာ")).toHaveCount(0);
  await page.getByRole("button", { name: "History", exact: true }).click();
  await page
    .locator("#assistant-history")
    .getByRole("button", { name: /အဖွဲ့ဝင်/ })
    .click();
  await expect(page.getByRole("log")).toContainText("ငွေပေးချေမှု စာရင်း ရှာ");

  await page
    .locator("#assistant-message")
    .fill(
      "Create a member first_name=Browser last_name=Isolated join_date=2026-11-12",
    );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page
    .getByRole("button", { name: "Confirm change", exact: true })
    .click();
  await expect(page.getByRole("log")).toContainText("Browser");
  await expect(page.getByRole("log")).not.toContainText("workflow-state v1");

  await page.locator("#assistant-message").fill("simulate provider failure");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("log")).toContainText(
    /could not continue|Nothing new was saved/,
  );
  await expect(page.getByRole("log")).toContainText("Browser");
  await expect(page.getByRole("log")).not.toContainText("workflow-state v1");

  await page.reload();
  await expect(page.getByRole("log")).toContainText("Browser");
  await expect(page.getByRole("log")).toContainText(
    "simulate provider failure",
  );
  await expect(page.getByRole("log")).toContainText(
    /could not continue|Nothing new was saved/,
  );
  await expect(page.getByRole("log")).not.toContainText("workflow-state v1");
  const members = await page.request.get(
    "/api/operations/members/?search=Browser&limit=20",
  );
  expect(members.ok(), await members.text()).toBeTruthy();
  expect(await members.text()).toContain("Isolated");
});

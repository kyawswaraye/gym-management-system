import { expect, type Page, test } from "@playwright/test";

import { ownerAccount, receptionAccount, signIn } from "./auth";

test.describe.configure({ mode: "serial" });

const TOOL_NAME = /\b(?:search|get|create|submit|approve)_[a-z0-9_]+\b/;

function gymToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Yangon",
  }).format(new Date());
}

function assertNoToolNames(text: string): void {
  expect(text).not.toMatch(TOOL_NAME);
  expect(text).not.toContain("Invented Person");
  expect(text).not.toContain("99999");
  expect(text).not.toContain("1999-01-01");
}

async function openAssistant(page: Page): Promise<void> {
  await page.goto("/assistant");
  await expect(
    page.locator("header").getByText("Assistant", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("The assistant is unavailable.")).toHaveCount(0);
}

async function ask(page: Page, text: string): Promise<string> {
  const log = page.getByRole("log");
  await page.locator("#assistant-message").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const staff = log.locator("div > p.self-end").filter({ hasText: text });
  await expect(staff.last()).toBeVisible();
  const reply = staff
    .last()
    .locator("xpath=following-sibling::p[not(contains(@class,'self-end'))][1]");
  await expect(page.locator("#assistant-message")).toBeEnabled({
    timeout: 30_000,
  });
  await expect(log).not.toContainText("Working");
  await expect(reply).toBeVisible();
  const body = (await reply.innerText()).trim();
  assertNoToolNames(body);
  return body;
}

async function replayConfirm(page: Page, url: string): Promise<string> {
  return page.evaluate(async (confirmUrl) => {
    const csrfResponse = await fetch("/api/auth/csrf", {
      credentials: "same-origin",
    });
    const csrf = (await csrfResponse.json()) as {
      data?: { csrf_token?: string };
    };
    const token = csrf.data?.csrf_token ?? "";
    const response = await fetch(confirmUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
      },
      body: JSON.stringify({ confirmed: true }),
    });
    return response.text();
  }, url);
}

async function archivePlan(page: Page, code: string): Promise<void> {
  const status = await page.evaluate(async (planCode) => {
    const csrfResponse = await fetch("/api/auth/csrf", {
      credentials: "same-origin",
    });
    const csrf = (await csrfResponse.json()) as {
      data?: { csrf_token?: string };
    };
    const token = csrf.data?.csrf_token ?? "";
    const listed = await fetch("/api/operations/membership-plans/?limit=100", {
      credentials: "same-origin",
    });
    const body = (await listed.json()) as {
      data?:
        | { id: string; code: string }[]
        | { results?: { id: string; code: string }[] };
    };
    const rows = Array.isArray(body.data)
      ? body.data
      : (body.data?.results ?? []);
    const plan = rows.find((row) => row.code === planCode);
    if (!plan) return 404;
    const archived = await fetch(
      `/api/operations/membership-plans/${plan.id}/archive/`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": token,
        },
        body: "{}",
      },
    );
    return archived.status;
  }, code);
  expect(status).toBe(200);
}

test.describe("signed-in owner", () => {
  let ownerPage: Page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext();
    ownerPage = await context.newPage();
    await signIn(ownerPage, ownerAccount);
  });

  test.afterAll(async () => {
    await ownerPage.context().close();
  });

  test("owner opens the assistant and switches conversations", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await page.goto("/dashboard");
    await page.getByRole("link", { name: "Assistant", exact: true }).click();
    await expect(page).toHaveURL(/\/assistant$/);
    await expect(page.getByText("The assistant is unavailable.")).toHaveCount(
      0,
    );
    await expect(
      page.getByText("Ask about this gym. Changes wait for your confirmation."),
    ).toBeVisible();
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(
      page.getByText("Your conversations", { exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    const first = await ask(page, "Search members.");
    expect(first).toContain("E2Escan");
    expect(first).toContain("E2Eassigned");
    expect(first).toContain("E2Eother");

    await page
      .getByRole("button", { name: "New conversation", exact: true })
      .click();
    await expect(page.getByRole("log")).not.toContainText("E2Escan");
    await page.getByRole("button", { name: "History", exact: true }).click();
    await page.getByRole("button", { name: /Search members/ }).click();
    await expect(page.getByRole("log")).toContainText("E2Escan");
  });

  test("reads render stored E2E rows and leave empty domains empty", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await openAssistant(page);

    const payments = await ask(page, "Search payments.");
    expect(payments).toContain("30.00");
    expect(payments).not.toContain("Cleaning");
    expect(payments).not.toContain("5000");

    const plans = await ask(
      page,
      "Search membership plans, including inactive plans.",
    );
    expect(plans).toContain("E2ESCAN");
    expect(plans).toContain("E2EASSIGN");
    expect(plans).toContain("ACTIVE");

    const classes = await ask(
      page,
      "Search class occurrences and class bookings.",
    );
    expect(classes).not.toContain("limited to today");
    expect(classes).toContain("No stored record matches that.");

    const sales = await ask(page, "Search retail sales and expenses.");
    expect(sales).toContain("E2E-RETAIL");
    expect(sales).toContain("30.00");
    expect(sales).not.toContain("Cleaning");
    expect(sales).not.toContain("Marketing");

    const salary = await ask(page, "Search salary periods and cash sessions.");
    expect(salary).toContain("No stored record matches that.");
    expect(salary).not.toContain("2026-10-31");

    const commissions = await ask(page, "Search PT commissions.");
    expect(commissions).toContain("No stored record matches that.");
    expect(commissions).not.toContain("800.00");
  });

  test("burmese, myanmar digits, and missing member stay on stored data", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await openAssistant(page);

    const burmese = await ask(page, "အဖွဲ့ဝင် စာရင်း ပြပါ");
    expect(burmese).toMatch(/[\u1000-\u109F]/);
    expect(burmese).toContain("E2Escan");

    const digits = await ask(page, "Search payments. ၅၀၀၀");
    expect(digits).toContain("30.00");
    expect(digits).not.toContain("5000");

    const missing = await ask(page, "What did the member pay?");
    expect(missing).toContain("Which member? Tell me the member number.");
    expect(missing).not.toContain("30.00");
  });

  test("a rejected plan proposal does not create the plan", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await openAssistant(page);
    const today = gymToday();
    await ask(
      page,
      "Create a membership plan code=E2EREJECT name=RejectMe duration_value=30 duration_unit=DAY price=7.00",
    );
    await expect(
      page.getByRole("region").getByText("Amount 7.00", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("region").getByText("E2EREJECT")).toBeVisible();
    await expect(page.getByRole("log")).not.toContainText(
      "The change is complete.",
    );
    await page
      .getByRole("button", { name: "Reject change", exact: true })
      .click();
    await expect(page.getByRole("log")).toContainText(
      "The change was rejected.",
    );
    const plans = await ask(
      page,
      "Search membership plans, including inactive plans.",
    );
    expect(plans).not.toContain("E2EREJECT");
    expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test("confirming a plan once does not duplicate it", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await openAssistant(page);
    await ask(
      page,
      "Create a membership plan code=E2EONCE name=OncePlan duration_value=30 duration_unit=DAY price=8.00",
    );
    await expect(
      page.getByRole("region").getByText("Amount 8.00", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("region").getByText("E2EONCE")).toBeVisible();
    await expect(page.getByRole("log")).not.toContainText(
      "The change is complete.",
    );
    const confirmResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/assistant/confirmations/") &&
        response.url().includes("/confirm/") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Confirm change", exact: true })
      .click();
    const confirmed = await confirmResponse;
    await expect(page.getByRole("log")).toContainText(
      "The change is complete.",
    );
    const replay = await replayConfirm(page, confirmed.url());
    expect(replay).toContain("already complete");
    const plans = await ask(
      page,
      "Search membership plans, including inactive plans.",
    );
    expect(plans.match(/E2EONCE/g)?.length).toBe(1);
  });

  test("duplicate plan names ask for a code and a changed plan blocks confirmation", async () => {
    const page = ownerPage;
    test.setTimeout(180_000);
    await openAssistant(page);
    const today = gymToday();
    for (const code of ["E2EYOGA", "E2EYOGA2"]) {
      await page
        .getByRole("button", { name: "New conversation", exact: true })
        .click();
      await ask(
        page,
        `Create a membership plan code=${code} name=Yoga duration_value=30 duration_unit=DAY price=1000.00`,
      );
      await page
        .getByRole("button", { name: "Confirm change", exact: true })
        .click();
      await expect(page.getByRole("log")).toContainText(
        "The change is complete.",
      );
    }
    await page
      .getByRole("button", { name: "New conversation", exact: true })
      .click();
    const named = await ask(page, "Which membership plan is named Yoga?");
    expect(named).toContain("Which plan code?");
    expect(named).toContain("E2EYOGA");
    expect(named).toContain("E2EYOGA2");

    await page
      .getByRole("button", { name: "New conversation", exact: true })
      .click();
    await ask(
      page,
      `Create a membership for member_name='E2Eother Guest' plan_code=E2EYOGA start_date=${today}`,
    );
    await expect(
      page.getByRole("button", { name: "Confirm change", exact: true }),
    ).toBeVisible();
    await archivePlan(page, "E2EYOGA");
    await page
      .getByRole("button", { name: "Confirm change", exact: true })
      .click();
    await expect(page.getByText("That plan is not active.")).toBeVisible();
    await page
      .getByRole("button", { name: "New conversation", exact: true })
      .click();
    const plans = await ask(
      page,
      "Search membership plans, including inactive plans.",
    );
    expect(plans).toContain("E2EYOGA");
    expect(plans).toContain("INACTIVE");
  });

  test("an ambiguous member name asks for the member number", async () => {
    test.setTimeout(120_000);
    const page = ownerPage;
    await openAssistant(page);
    const today = gymToday();
    for (const _ of [1, 2]) {
      await page
        .getByRole("button", { name: "New conversation", exact: true })
        .click();
      await ask(
        page,
        `Create a member first_name=E2Etwin last_name=Pair join_date=${today}`,
      );
      await page
        .getByRole("button", { name: "Confirm change", exact: true })
        .click();
      await expect(page.getByRole("log")).toContainText(
        "The change is complete.",
      );
    }
    await page
      .getByRole("button", { name: "New conversation", exact: true })
      .click();
    const payment = await ask(page, "What did E2Etwin Pair pay?");
    expect(payment).toContain("Which member?");
    expect(payment).toMatch(/MEM-/);
    expect(payment).not.toContain("30.00");
  });

  test("logout blocks the assistant", async () => {
    const page = ownerPage;
    await page.getByRole("button", { name: /^Account,/ }).click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/assistant");
    await expect(page).toHaveURL(/\/login/);
  });
});

test("receptionist cannot approve a payment and the owner can", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const today = gymToday();
  await signIn(page, receptionAccount);
  await openAssistant(page);
  await ask(
    page,
    `Create a payment draft purpose=OTHER description='E2E-assistant-fee' amount=19.00 currency=THB method=CARD business_date=${today}`,
  );
  await expect(
    page.getByRole("region").getByText("Amount 19.00", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region").getByText("E2E-assistant-fee", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("log")).not.toContainText(
    "The change is complete.",
  );
  await page
    .getByRole("button", { name: "Confirm change", exact: true })
    .click();
  await expect(page.getByRole("log")).toContainText("The change is complete.");
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await ask(page, "Submit the payment description='E2E-assistant-fee'");
  await page
    .getByRole("button", { name: "Confirm change", exact: true })
    .click();
  await expect(page.getByRole("log")).toContainText("The change is complete.");
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  const denied = await ask(
    page,
    "Approve the payment description='E2E-assistant-fee'",
  );
  expect(denied).not.toContain("The change is complete.");
  await expect(
    page.getByRole("region").getByText("Amount 19.00", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm change", exact: true })
    .click();
  await expect(page.getByRole("log")).not.toContainText(
    "The change is complete.",
  );
  await expect(
    page.getByText("You do not have permission to perform this action."),
  ).toBeVisible();

  await page.getByRole("button", { name: /^Account,/ }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await signIn(page, ownerAccount);
  await openAssistant(page);
  const before = await ask(page, "Search payments.");
  const pendingFee = before
    .split(";")
    .find((part) => part.includes("E2E-assistant-fee"));
  expect(pendingFee).toBeTruthy();
  expect(pendingFee).toContain("19.00");
  expect(pendingFee).not.toContain("POSTED");
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await ask(page, "Approve the payment description='E2E-assistant-fee'");
  await expect(
    page.getByRole("region").getByText("Amount 19.00", { exact: true }),
  ).toBeVisible();
  const record = page.getByRole("region").getByText(/^Record /);
  await expect(record).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm change", exact: true })
    .click();
  await expect(page.getByRole("log")).toContainText("The change is complete.");
  const after = await ask(page, "Search payments.");
  const postedFee = after
    .split(";")
    .find((part) => part.includes("E2E-assistant-fee"));
  expect(postedFee).toContain("POSTED");
  expect(after.match(/E2E-assistant-fee/g)?.length).toBe(1);
});

test("a foreign gym id and an unknown member id stay out of the reply", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await signIn(page, ownerAccount);
  await openAssistant(page);
  const missing = await ask(
    page,
    "Get the member member_id=11111111-1111-1111-1111-111111111111",
  );
  expect(missing).toContain("No stored record matches that.");
  const foreign = await ask(
    page,
    "Search members gym_id=22222222-2222-2222-2222-222222222222",
  );
  expect(foreign).toContain("E2Escan");
  expect(foreign).not.toContain("Local Test Gym");
  expect(foreign).not.toContain("22222222-2222-2222-2222-222222222222");
});

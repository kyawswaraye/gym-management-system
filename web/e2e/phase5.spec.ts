import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  managerAccount,
  ownerAccount,
  receptionAccount,
  signIn,
  trainerAccount,
  watchJsonResponses,
  type E2EAccount,
} from "./auth";

const ZONE = "Asia/Yangon";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const API_URL =
  process.env.DJANGO_API_URL ??
  `http://127.0.0.1:${process.env.E2E_API_PORT ?? "8010"}`;

test.describe.configure({ mode: "serial", timeout: 240_000 });

test.beforeEach(() => {
  test.setTimeout(240_000);
});

test("owner assigns trainers and prepares the catalogs", async ({ page }) => {
  const accounts = [ownerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, ownerAccount);
  const links = page.getByRole("navigation", { name: "Primary" });
  await expect(
    links.getByRole("link", { name: "Check-ins", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Check-in QR", exact: true }),
  ).toHaveCount(0);
  await links.getByRole("button", { name: "Training", exact: true }).click();
  await expect(
    links.getByRole("link", { name: "Commissions", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "PT sales", exact: true }),
  ).toBeVisible();

  await page.goto("/pt/trainers");
  await expect(
    page.getByRole("heading", { name: "Trainers", exact: true }),
  ).toBeVisible();
  await assign(page, "E2E Trainer (E2ETRN)", "E2Escan", /E2Escan Visitor/);
  await assign(page, "E2E Bea (E2EBEA)", "E2Escan", /E2Escan Visitor/);
  await assign(
    page,
    "E2E Trainer (E2ETRN)",
    "E2Eassigned",
    /E2Eassigned Member/,
  );

  await page.goto("/pt/packages");
  await savePackage(page, {
    type: "PT",
    name: "E2E PT pack",
    sessions: "4",
    price: "80.00",
  });

  await page.goto("/classes");
  await page.getByRole("tab", { name: "Class types", exact: true }).click();
  await page
    .getByRole("button", { name: "Create class type", exact: true })
    .click();
  const classType = titledForm(page, "create-class-type-title");
  await expect(classType).toBeVisible();
  await classType.getByLabel("Name", { exact: true }).fill("E2E Cycle");
  await classType
    .getByLabel("Default duration minutes", { exact: true })
    .fill("60");
  await classType.getByLabel("Default capacity", { exact: true }).fill("4");
  await runCommand(page, /\/class-types\/?$/, () =>
    classType
      .getByRole("button", { name: "Create class type", exact: true })
      .click(),
  );
  await expect(classType).toBeHidden();

  await page.goto("/pt/packages");
  await savePackage(page, {
    type: "CLASS",
    name: "E2E Class pack",
    sessions: "4",
    price: "0.00",
    classType: /E2E_CYCLE/,
  });

  await page.goto("/pt/member-packages");
  await page
    .getByRole("button", { name: "Grant package", exact: true })
    .click();
  const grant = titledForm(page, "grant-title");
  await expect(grant).toBeVisible();
  await pickMember(grant, "E2Escan", /E2Escan Visitor/);
  await selectByText(combo(grant, "Package"), "CLASS · E2E_CLASS_PACK");
  await grant.getByLabel("Reason", { exact: true }).fill("E2E class grant");
  await runCommand(page, /\/member-service-packages\/?$/, () =>
    grant.getByRole("button", { name: "Save grant", exact: true }).click(),
  );
  await expect(grant).toBeHidden();

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("reception submits packaged cash sales and cannot approve", async ({
  page,
}) => {
  const accounts = [receptionAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, receptionAccount);
  const links = page.getByRole("navigation", { name: "Primary" });
  await links.getByRole("button", { name: "Training", exact: true }).click();
  await expect(
    links.getByRole("link", { name: "PT sales", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Commissions", exact: true }),
  ).toHaveCount(0);
  await links.getByRole("button", { name: "Finance", exact: true }).click();
  await expect(
    links.getByRole("link", { name: "Cash book", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Check-ins", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Check-in QR", exact: true }),
  ).toHaveCount(0);

  await openTill(page);

  await page.goto("/pt/sales");
  await createSale(page, {
    trainer: "E2E Trainer (E2ETRN)",
    description: "E2E consumed pack",
    gross: "100.00",
    packageLabel: "E2E_PT_PACK",
    payment: "100.00",
  });
  await submitOpenSale(page);
  await expect(saleDetail(page)).toContainText("PENDING_APPROVAL");
  await expect(
    page.getByRole("button", { name: "Approve sale", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Void sale", exact: true }),
  ).toHaveCount(0);

  await createSale(page, {
    trainer: "E2E Bea (E2EBEA)",
    description: "E2E bea session",
    gross: "20.00",
  });
  await submitOpenSale(page);
  await expect(saleDetail(page)).toContainText("PENDING_APPROVAL");

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("owner approves reception sales with the open cash session", async ({
  page,
}) => {
  const accounts = [ownerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, ownerAccount);
  await page.goto("/pt/sales");
  await openSale(page, "100.00");
  await approveCashSale(page);
  await expect(saleDetail(page)).toContainText("POSTED");
  await expect(saleDetail(page)).toContainText("CASH");
  await openSale(page, "20.00");
  await page.getByRole("button", { name: "Approve sale", exact: true }).click();
  const approve = titledForm(page, "approve-title");
  await expect(approve).toBeVisible();
  await expect(approve).toContainText("no cash session is sent");
  await runCommand(page, /\/pt-sales\/[^/]+\/approve\/?$/, () =>
    approve
      .getByRole("button", { name: "Confirm approval", exact: true })
      .click(),
  );
  await expect(saleDetail(page)).toContainText("POSTED");

  await page.goto("/pt/member-packages");
  const grant = page.locator("li").filter({ hasText: "E2E_PT_PACK" });
  await expect(grant).toHaveCount(1);
  await grant.getByRole("button").click();
  await expect(
    page.getByRole("region", { name: "Session ledger" }),
  ).toContainText("GRANT · 4");

  await page.goto("/pt/commissions");
  await expect(
    page.getByRole("button", { name: "Outstanding 10.00 THB", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Outstanding 5.00 THB", exact: true }),
  ).toBeVisible();

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("manager pays the trainer commission and a consumed sale does not void", async ({
  page,
}) => {
  const accounts = [managerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, managerAccount);
  await page.goto("/pt/commissions");
  await page
    .getByRole("button", { name: "Outstanding 10.00 THB", exact: true })
    .click();
  await recordPayout(page, "4.00");
  await expect(commissionDetail(page)).toContainText("PARTIALLY_PAID");
  await expect(commissionDetail(page)).toContainText("6.00");
  await recordPayout(page, "6.00");
  await expect(commissionDetail(page)).toContainText("PAID");
  await expect(commissionDetail(page)).toContainText("4.00 CASH");
  await expect(commissionDetail(page)).toContainText("6.00 CASH");
  await expect(
    page.getByRole("button", { name: "Outstanding 5.00 THB", exact: true }),
  ).toBeVisible();

  const past = pastPt(gymWall());
  await page.goto("/pt/bookings");
  await page.getByRole("button", { name: "Book session", exact: true }).click();
  const booking = titledForm(page, "book-pt-title");
  await expect(booking).toBeVisible();
  await selectByText(combo(booking, "Trainer"), "E2E Trainer (E2ETRN)");
  await pickMember(booking, "E2Escan", /E2Escan Visitor/);
  await booking
    .getByLabel("Starts", { exact: true })
    .fill(`${past.date}T${past.start}`);
  await booking
    .getByLabel("Ends", { exact: true })
    .fill(`${past.date}T${past.end}`);
  await selectByText(combo(booking, "Member package"), "E2E_PT_PACK");
  await runCommand(page, /\/pt-bookings\/?$/, () =>
    booking.getByRole("button", { name: "Save booking", exact: true }).click(),
  );
  await expect(booking).toBeHidden();
  await runCommand(page, /\/pt-bookings\/[^/]+\/attend\/?$/, () =>
    page.getByRole("button", { name: "Mark attended", exact: true }).click(),
  );

  await page.goto("/pt/sales");
  await openSale(page, "100.00");
  await page.getByRole("button", { name: "Void sale", exact: true }).click();
  const voidForm = titledForm(page, "void-sale-title");
  await expect(voidForm).toBeVisible();
  await voidForm
    .getByLabel("Void reason", { exact: true })
    .fill("Consumed pack");
  await chooseSession(voidForm, "Cash session for the payment reversal");
  await chooseSession(voidForm, "Payout destination for 4.00");
  await chooseSession(voidForm, "Payout destination for 6.00");
  const denied = waitForCommand(page, /\/pt-sales\/[^/]+\/void\/?$/);
  await voidForm
    .getByRole("button", { name: "Confirm void", exact: true })
    .click();
  const deniedResponse = await denied;
  const deniedBody = await diagnosticBody(deniedResponse);
  expect(deniedResponse.status(), deniedBody).toBe(409);
  expect(deniedBody).toContain("PACKAGE_CONSUMED");
  await expect(page.locator("p.error[role='alert']")).toContainText(
    "PACKAGE_CONSUMED",
  );

  await page.reload();
  await openSale(page, "100.00");
  await expect(saleDetail(page)).toContainText("POSTED");
  await page.goto("/pt/commissions");
  await page
    .getByRole("button", { name: "Outstanding 0.00 THB", exact: true })
    .click();
  await expect(commissionDetail(page)).toContainText("PAID");
  await expect(commissionDetail(page)).toContainText("4.00 CASH");
  await expect(commissionDetail(page)).toContainText("POSTED");
  await page.goto("/pt/member-packages");
  const consumed = page
    .locator("li")
    .filter({ hasText: "ACTIVE · remaining 3 of 4" });
  await consumed.getByRole("button", { name: /E2E_PT_PACK/ }).click();
  const ledger = page.getByRole("region", { name: "Session ledger" });
  await expect(ledger).toContainText("GRANT · 4");
  await expect(ledger).toContainText("ATTENDED · -1");
  await expect(ledger).not.toContainText("REFUND");
  await page.goto("/cash-book");
  await page.getByRole("button", { name: "MAIN", exact: true }).click();
  await expect(movements(page).getByText("E2E consumed pack")).toBeVisible();
  await expect(
    movements(page).getByRole("cell", { name: /100\.00\s*THB/ }),
  ).toHaveCount(1);

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("reception submits the untouched packaged sale", async ({ page }) => {
  const accounts = [receptionAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, receptionAccount);
  await page.goto("/pt/sales");
  await createSale(page, {
    trainer: "E2E Trainer (E2ETRN)",
    description: "E2E untouched pack",
    gross: "80.00",
    packageLabel: "E2E_PT_PACK",
    payment: "80.00",
  });
  await submitOpenSale(page);
  await expect(saleDetail(page)).toContainText("PENDING_APPROVAL");
  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("owner approves the untouched sale", async ({ page }) => {
  const accounts = [ownerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, ownerAccount);
  await page.goto("/pt/sales");
  await openSale(page, "80.00");
  await approveCashSale(page);
  await expect(saleDetail(page)).toContainText("POSTED");
  await page.goto("/pt/member-packages");
  await expect(
    page.locator("li").filter({ hasText: "E2E_PT_PACK" }),
  ).toHaveCount(2);
  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("manager voids the untouched sale and runs the class cutoff", async ({
  page,
}) => {
  const accounts = [managerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, managerAccount);
  await page.goto("/pt/sales");
  await openSale(page, "80.00");
  await page.getByRole("button", { name: "Void sale", exact: true }).click();
  const voidForm = titledForm(page, "void-sale-title");
  await expect(voidForm).toBeVisible();
  await voidForm
    .getByLabel("Void reason", { exact: true })
    .fill("Unused packaged sale");
  await chooseSession(voidForm, "Cash session for the payment reversal");
  await expect(voidForm.getByText("Payout destination")).toHaveCount(0);
  const voided = waitForCommand(page, /\/pt-sales\/[^/]+\/void\/?$/);
  await voidForm
    .getByRole("button", { name: "Confirm void", exact: true })
    .click();
  const voidedResponse = await voided;
  const voidedBody = await diagnosticBody(voidedResponse);
  expect(voidedResponse.ok(), voidedBody).toBeTruthy();
  await expectNotice(page, "Sale voided.");
  await expect(saleDetail(page)).toContainText("VOID");

  await page.goto("/pt/member-packages");
  const refunded = page
    .locator("li")
    .filter({ hasText: "VOID · remaining 0 of 4" });
  await refunded.getByRole("button", { name: /E2E_PT_PACK/ }).click();
  const ledger = page.getByRole("region", { name: "Session ledger" });
  await expect(ledger).toContainText("GRANT · 4");
  await expect(ledger).toContainText("REFUND · -4");
  const consumed = page
    .locator("li")
    .filter({ hasText: "ACTIVE · remaining 3 of 4" });
  await consumed.getByRole("button", { name: /E2E_PT_PACK/ }).click();
  await expect(ledger).toContainText("ATTENDED · -1");
  await expect(ledger).not.toContainText("REFUND");

  await page.goto("/cash-book");
  await page.getByRole("button", { name: "MAIN", exact: true }).click();
  const table = movements(page);
  await expect(
    table.getByText("E2E consumed pack", { exact: true }),
  ).toBeVisible();
  await expect(
    table.getByText("E2E untouched pack", { exact: true }),
  ).toBeVisible();
  await expect(table.getByRole("cell", { name: /100\.00\s*THB/ })).toHaveCount(
    1,
  );
  await expect(table.getByRole("cell", { name: /80\.00\s*THB/ })).toHaveCount(
    2,
  );
  await expect(table.getByText("Out", { exact: true })).toHaveCount(3);

  const now = gymWall();
  const tomorrow = addDays(now.date, 1);
  const late = lateClass(now);
  const finished = finishedClasses(now);
  await page.goto("/classes");
  await createSchedule(page, {
    weekday: weekdayOf(tomorrow),
    start: "10:00",
    end: "11:00",
    from: tomorrow,
    location: "E2E Early",
  });
  await createSchedule(page, {
    weekday: weekdayOf(finished.date),
    start: finished.attend.start,
    end: finished.attend.end,
    from: finished.date,
    location: "E2E Attend",
  });
  await createSchedule(page, {
    weekday: weekdayOf(finished.date),
    start: finished.noshow.start,
    end: finished.noshow.end,
    from: finished.date,
    location: "E2E NoShow",
  });
  await createSchedule(page, {
    weekday: late.weekday,
    start: late.start,
    end: late.end,
    from: late.date,
    location: "E2E Late",
  });
  if (finished.date < now.date) {
    await showOccurrencesFrom(page, finished.date);
  }
  await bookClass(page, /E2E Early/);
  await cancelClassBooking(page, "Before cutoff", 200);
  await bookClass(page, /E2E Attend/);
  await runCommand(page, /\/class-bookings\/[^/]+\/attend\/?$/, () =>
    page.getByRole("button", { name: "Mark attended", exact: true }).click(),
  );
  await bookClass(page, /E2E NoShow/);
  await runCommand(page, /\/class-bookings\/[^/]+\/no-show\/?$/, () =>
    page.getByRole("button", { name: "Mark no-show", exact: true }).click(),
  );
  await bookClass(page, /E2E Late/);
  await expect(
    page.getByRole("button", { name: "Cancel booking", exact: true }),
  ).toHaveCount(1);

  await page.goto("/pt/member-packages");
  await page
    .locator("li")
    .filter({ hasText: "E2E_CLASS_PACK" })
    .getByRole("button")
    .click();
  await expect(
    page.getByRole("region", { name: "Session ledger" }),
  ).toContainText("ATTENDED · -1");
  await expect(
    page.getByRole("region", { name: "Session ledger" }),
  ).toContainText("POLICY_NO_SHOW · -1");
  await expect(
    page.locator("li").filter({ hasText: "remaining 2 of 4" }),
  ).toBeVisible();

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("reception cannot cancel inside the class cutoff", async ({ page }) => {
  const accounts = [receptionAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, receptionAccount);
  await page.goto("/classes");
  await expect(
    page.getByRole("button", { name: "Cancel booking", exact: true }),
  ).toHaveCount(1);
  await cancelClassBooking(page, "Inside cutoff", 409);
  await expect(page.locator("p.error[role='alert']")).toContainText(
    "CLASS_CUTOFF",
  );
  await expect(
    page.getByRole("button", { name: "Cancel booking", exact: true }),
  ).toHaveCount(1);
  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("manager cancels inside the cutoff when a reason is entered", async ({
  page,
}) => {
  const accounts = [managerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, managerAccount);
  await page.goto("/classes");
  await cancelClassBooking(page, "Inside cutoff approved", 200);
  await expect(
    page.getByRole("button", { name: "Cancel booking", exact: true }),
  ).toHaveCount(0);
  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

test("trainer sees only one commission and scans an assigned member", async ({
  page,
  playwright,
}) => {
  const accounts = [ownerAccount, trainerAccount];
  const assertResponses = watchJsonResponses(page, secretValues(accounts));
  await signIn(page, ownerAccount);
  const firstCode = await issueChallenge(page);
  await signOut(page);

  await signIn(page, trainerAccount);
  const links = page.getByRole("navigation", { name: "Primary" });
  await links.getByRole("button", { name: "Training", exact: true }).click();
  await expect(
    links.getByRole("link", { name: "Commissions", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Check-ins", exact: true }),
  ).toBeVisible();
  await expect(
    links.getByRole("link", { name: "Check-in QR", exact: true }),
  ).toHaveCount(0);
  await expect(
    links.getByRole("link", { name: "Cash book", exact: true }),
  ).toHaveCount(0);
  await page.goto("/pt/commissions");
  await expect(page.getByRole("button", { name: /Outstanding / })).toHaveCount(
    2,
  );
  await expect(
    page.getByRole("button", { name: "Outstanding 5.00 THB", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Outstanding 0.00 THB", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Record payout", exact: true }),
  ).toHaveCount(0);

  await page.goto("/check-ins");
  await expect(
    page.getByRole("heading", { name: "Trainer QR scan", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Scan check-in code", exact: true })
    .click();
  const scan = page.getByRole("dialog", { name: "Trainer QR scan" });
  await scan
    .getByLabel("Search assigned members", { exact: true })
    .fill("E2Eother");
  await expect(combo(scan, "Assigned member")).not.toContainText("E2Eother");
  await scan.getByLabel("Search assigned members", { exact: true }).fill("");
  await selectByText(combo(scan, "Assigned member"), "E2Eassigned Member");
  await scan.getByLabel("Check-in code", { exact: true }).fill(firstCode);
  await scan
    .getByRole("button", { name: "Scan check-in code", exact: true })
    .click();
  await expectNotice(page, "Checked in from the trainer code.");
  await expect(
    page.getByRole("dialog", { name: "Trainer QR scan" }),
  ).toHaveCount(0);
  await expect(page.getByText(firstCode, { exact: true })).toHaveCount(0);
  const storedCode = await page.evaluate(() => {
    const blob = JSON.stringify({
      localStorage: Object.entries(localStorage),
      sessionStorage: Object.entries(sessionStorage),
      cookie: document.cookie,
    });
    return blob.includes("gms-checkin:v1:");
  });
  expect(storedCode).toBe(false);
  await signOut(page);

  await signIn(page, ownerAccount);
  const secondCode = await issueChallenge(page);
  const otherMemberId = await readMemberId(page, "E2Eother");
  await signOut(page);
  await signIn(page, trainerAccount);
  const hidden = await scanUnassigned(page, secondCode, otherMemberId);
  expect(hidden.status, hidden.body).toBe(404);
  expect(hidden.body).not.toContain("gms-checkin:v1:");
  expect(hidden.checkIns).toBe(0);
  await signOut(page);

  await signIn(page, ownerAccount);
  await page.goto("/check-ins");
  await page
    .getByLabel("Search active members", { exact: true })
    .fill("E2Eother");
  await page
    .getByRole("list", { name: "Matching members" })
    .getByRole("button", { name: /E2Eother Guest/ })
    .click();
  await expect(
    page.getByRole("region", { name: /E2Eother Guest/ }),
  ).not.toContainText("Open");
  await page
    .getByLabel("Search active members", { exact: true })
    .fill("E2Eassigned");
  await page
    .getByRole("list", { name: "Matching members" })
    .getByRole("button", { name: /E2Eassigned Member/ })
    .click();
  await expect(
    page.getByRole("region", { name: /E2Eassigned Member/ }),
  ).toContainText("Open");

  const api = await playwright.request.newContext({ baseURL: API_URL });
  try {
    const missing = await api.post("/api/v1/check-ins/scan/", {
      data: { member_id: "00000000-0000-0000-0000-000000000000" },
    });
    const missingBody = redact(await missing.text());
    expect(missing.status(), missingBody).toBe(404);
  } finally {
    await api.dispose();
  }

  await expectNoBrowserSecrets(page, secretValues(accounts));
  await assertResponses();
});

function secretValues(accounts: readonly E2EAccount[]): string[] {
  return accounts.flatMap((account) => [account.password, account.totpSecret]);
}

function redactSecrets(body: string): string {
  if (
    /"(?:access_token|refresh_token|mfa_challenge_token|invitation_token|gym_selection_ticket)"/.test(
      body,
    )
  ) {
    return "[redacted credential response]";
  }
  return body.replace(/gms-checkin:v1:[A-Za-z0-9_-]+/g, "[check-in-code]");
}

function redact(body: string): string {
  return redactSecrets(body).slice(0, 500);
}

function gymWall(instant = new Date()): {
  date: string;
  hours: number;
  minutes: number;
} {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ZONE,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(instant);
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  const hourText = read("hour");
  return {
    date: `${read("year")}-${read("month")}-${read("day")}`,
    hours: hourText === "24" ? 0 : Number(hourText),
    minutes: Number(read("minute")),
  };
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function weekdayOf(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  return (date.getUTCDay() + 6) % 7;
}

function clock(hours: number, minutes: number): string {
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function minutesOf(clockText: string): number {
  const [hours, minutes] = clockText.split(":").map(Number);
  return (hours ?? 0) * 60 + (minutes ?? 0);
}

function fromMinutes(total: number): string {
  return clock(Math.floor(total / 60), total % 60);
}

function finishedClasses(now: {
  date: string;
  hours: number;
  minutes: number;
}): {
  date: string;
  attend: { start: string; end: string };
  noshow: { start: string; end: string };
} {
  const lateStart = minutesOf(lateClass(now).start);
  const end = lateStart - 2;
  // Same-day packing needs ~54 minutes before late so pastPt (30m) and
  // two finished classes fit without overlapping the member's late booking.
  if (end >= 54) {
    return {
      date: now.date,
      attend: { start: fromMinutes(end - 22), end: fromMinutes(end - 12) },
      noshow: { start: fromMinutes(end - 10), end: fromMinutes(end) },
    };
  }
  return {
    date: addDays(now.date, -1),
    attend: { start: "20:00", end: "20:10" },
    noshow: { start: "20:12", end: "20:22" },
  };
}

function pastPt(now: { date: string; hours: number; minutes: number }): {
  date: string;
  start: string;
  end: string;
} {
  const finished = finishedClasses(now);
  if (finished.date === now.date) {
    const attendStart = minutesOf(finished.attend.start);
    return {
      date: now.date,
      start: fromMinutes(attendStart - 32),
      end: fromMinutes(attendStart - 2),
    };
  }
  // Finished classes moved to yesterday. Keep a tiny same-day PT that ends
  // before the in-progress late class so member bookings do not collide.
  const lateStart = minutesOf(lateClass(now).start);
  if (lateStart >= 3) {
    return { date: now.date, start: "00:00", end: "00:01" };
  }
  return {
    date: addDays(now.date, -1),
    start: "19:00",
    end: "19:30",
  };
}

function lateClass(now: { date: string; hours: number; minutes: number }): {
  date: string;
  start: string;
  end: string;
  weekday: number;
} {
  let hours = now.hours;
  let minutes = now.minutes - 15;
  if (minutes < 0) {
    hours -= 1;
    minutes += 60;
  }
  if (hours < 0) {
    return {
      date: now.date,
      start: "00:05",
      end: "00:25",
      weekday: weekdayOf(now.date),
    };
  }
  let endHours = hours;
  let endMinutes = minutes + 20;
  if (endMinutes >= 60) {
    endHours += 1;
    endMinutes -= 60;
  }
  if (endHours > 23) {
    return {
      date: now.date,
      start: clock(hours, minutes),
      end: "23:59",
      weekday: weekdayOf(now.date),
    };
  }
  return {
    date: now.date,
    start: clock(hours, minutes),
    end: clock(endHours, endMinutes),
    weekday: weekdayOf(now.date),
  };
}

function titledForm(page: Page, titleId: string): Locator {
  return page.locator(`form:has(#${titleId})`);
}

function combo(scope: Page | Locator, name: string): Locator {
  return scope.getByRole("combobox", { name, exact: true });
}

function saleDetail(page: Page): Locator {
  return page.getByRole("region", { name: "Sale detail" });
}

function commissionDetail(page: Page): Locator {
  return page.getByRole("region", { name: "Commission detail" });
}

function movements(page: Page): Locator {
  return page.getByRole("table", { name: "Cash movements" });
}

function waitForCommand(page: Page, pattern: RegExp) {
  return page.waitForResponse(
    (response) => {
      const status = response.status();
      if (status >= 300 && status < 400) return false;
      return (
        response.request().method() === "POST" &&
        pattern.test(new URL(response.url()).pathname)
      );
    },
    { timeout: 30_000 },
  );
}

async function diagnosticBody(response: {
  status: () => number;
  text: () => Promise<string>;
}): Promise<string> {
  try {
    return redact(await response.text());
  } catch {
    return `status ${response.status()} body unavailable`;
  }
}

async function runCommand(
  page: Page,
  pattern: RegExp,
  act: () => Promise<void>,
): Promise<void> {
  const pending = waitForCommand(page, pattern);
  await act();
  const response = await pending;
  const body = await diagnosticBody(response);
  expect(response.ok(), body).toBeTruthy();
}

async function expectNotice(page: Page, text: string): Promise<void> {
  const notice = page.locator("[aria-live='polite']").filter({ hasText: text });
  try {
    await expect(notice).toBeVisible();
  } catch (error) {
    const alerts = await page.locator("p.error[role='alert']").allInnerTexts();
    throw new Error(
      `Expected notice ${JSON.stringify(text)}. Alerts: ${alerts.join(" | ") || "(none)"}`,
      { cause: error },
    );
  }
}

async function signOut(page: Page): Promise<void> {
  const signOutButton = page.getByRole("button", {
    name: "Sign out",
    exact: true,
  });
  if (!(await signOutButton.isVisible())) {
    await page.locator("footer").getByRole("button").click();
  }
  await signOutButton.click();
  await expect(
    page.getByRole("heading", { name: "Sign in", exact: true }),
  ).toBeVisible();
}

async function selectByText(select: Locator, text: string): Promise<void> {
  await expect(select).toBeVisible();
  let value = "";
  await expect
    .poll(async () => {
      value = await select.evaluate((element, needle) => {
        const options = Array.from((element as HTMLSelectElement).options);
        return (
          options.find((option) => option.text.includes(needle))?.value ?? ""
        );
      }, text);
      return value;
    })
    .not.toBe("");
  await select.selectOption(value);
}

async function pickMember(
  scope: Locator,
  query: string,
  name: RegExp,
): Promise<void> {
  const search = scope.getByLabel(/Search/);
  await search.fill(query);
  const choice = scope
    .getByRole("list", { name: "Matching members" })
    .getByRole("button", { name });
  await expect(choice).toBeVisible();
  await choice.click();
  await expect(scope.getByText(/Selected member:/)).toContainText(query);
}

async function openTill(page: Page): Promise<void> {
  await page.goto("/cash-book");
  await page.getByRole("button", { name: "Open session", exact: true }).click();
  const open = page.getByRole("dialog", { name: "Open session" });
  await open.getByLabel(/Opening cash/).fill("500.00");
  await runCommand(page, /\/cash-sessions\/open\/?$/, () =>
    open.getByRole("button", { name: "Open session", exact: true }).click(),
  );
  await expect(
    page.getByRole("region", { name: "Session detail" }),
  ).toContainText("MAIN");
}

async function assign(
  page: Page,
  trainer: string,
  query: string,
  member: RegExp,
): Promise<void> {
  await page
    .getByRole("button", { name: "Create assignment", exact: true })
    .click();
  const form = titledForm(page, "create-assignment-title");
  await expect(form).toBeVisible();
  await selectByText(combo(form, "Trainer"), trainer);
  await pickMember(form, query, member);
  // Cover yesterday so near-midnight past PT / finished class helpers stay valid.
  await form
    .getByLabel("Effective from", { exact: true })
    .fill(addDays(gymWall().date, -1));
  await runCommand(page, /\/trainer-assignments\/?$/, () =>
    form.getByRole("button", { name: "Save assignment", exact: true }).click(),
  );
  await expect(form).toBeHidden();
}

async function savePackage(
  page: Page,
  input: {
    type: "PT" | "CLASS";
    name: string;
    sessions: string;
    price: string;
    classType?: RegExp;
  },
): Promise<void> {
  await page
    .getByRole("button", { name: "Create package", exact: true })
    .click();
  const form = titledForm(page, "create-package-title");
  await expect(form).toBeVisible();
  await combo(form, "Type").selectOption(input.type);
  await form.getByLabel("Name", { exact: true }).fill(input.name);
  await form.getByLabel("Sessions", { exact: true }).fill(input.sessions);
  await form.getByLabel("Validity", { exact: true }).fill("3");
  await form.getByLabel("Price", { exact: true }).fill(input.price);
  if (input.classType) {
    await selectByText(
      combo(form, "Class type for a CLASS package"),
      "E2E_CYCLE",
    );
  }
  await runCommand(page, /\/service-packages\/?$/, () =>
    form.getByRole("button", { name: "Save package", exact: true }).click(),
  );
  await expect(form).toBeHidden();
}

async function createSale(
  page: Page,
  input: {
    trainer: string;
    description: string;
    gross: string;
    packageLabel?: string;
    payment?: string;
  },
): Promise<void> {
  await page.getByRole("button", { name: "Create draft", exact: true }).click();
  const form = titledForm(page, "sale-form-title");
  await expect(form).toBeVisible();
  await pickMember(form, "E2Escan", /E2Escan Visitor/);
  await selectByText(combo(form, "Trainer"), input.trainer);
  await form.getByLabel("Description", { exact: true }).fill(input.description);
  await form.getByLabel("Gross fee", { exact: true }).fill(input.gross);
  if (input.packageLabel) {
    await selectByText(combo(form, "PT package"), input.packageLabel);
  }
  if (input.payment) {
    await combo(form, "Payment draft").selectOption("set");
    await form
      .getByLabel("Payment description", { exact: true })
      .fill(input.description);
    await form
      .getByLabel("Payment amount", { exact: true })
      .fill(input.payment);
    await combo(form, "Payment method").selectOption("CASH");
  }
  await runCommand(page, /\/pt-sales\/?$/, () =>
    form.getByRole("button", { name: "Save draft", exact: true }).click(),
  );
  await expect(form).toBeHidden();
}

async function submitOpenSale(page: Page): Promise<void> {
  await runCommand(page, /\/pt-sales\/[^/]+\/submit\/?$/, () =>
    page.getByRole("button", { name: "Submit sale", exact: true }).click(),
  );
  await expect(saleDetail(page)).toContainText("PENDING_APPROVAL");
}

async function openSale(page: Page, gross: string): Promise<void> {
  await page.getByRole("button", { name: new RegExp(`${gross} THB`) }).click();
  await expect(saleDetail(page)).toBeVisible();
}

async function chooseSession(scope: Locator, label: string): Promise<void> {
  await selectByText(combo(scope, label), "MAIN");
}

async function approveCashSale(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Approve sale", exact: true }).click();
  const form = titledForm(page, "approve-title");
  await expect(form).toBeVisible();
  await chooseSession(form, "Cash session for the payment");
  await runCommand(page, /\/pt-sales\/[^/]+\/approve\/?$/, () =>
    form.getByRole("button", { name: "Confirm approval", exact: true }).click(),
  );
  await expect(form).toBeHidden();
  await expect(saleDetail(page)).toContainText("POSTED");
}

async function recordPayout(page: Page, amount: string): Promise<void> {
  await page
    .getByRole("button", { name: "Record payout", exact: true })
    .click();
  const form = titledForm(page, "payout-title");
  await expect(form).toBeVisible();
  await form.getByLabel("Amount", { exact: true }).fill(amount);
  await chooseSession(form, "Cash session when the method is cash");
  await runCommand(page, /\/pt-commissions\/[^/]+\/record-payment\/?$/, () =>
    form.getByRole("button", { name: "Save payout", exact: true }).click(),
  );
  await expect(form).toBeHidden();
}

async function showOccurrencesFrom(page: Page, from: string): Promise<void> {
  await page.getByRole("tab", { name: "This week", exact: true }).click();
  const rangeForm = page.locator("form").filter({
    has: page.getByRole("button", { name: "Show occurrences", exact: true }),
  });
  await rangeForm.getByLabel("From", { exact: true }).fill(from);
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.ok() &&
        response.request().method() === "GET" &&
        /\/class-occurrences\/?/.test(new URL(response.url()).pathname) &&
        new URL(response.url()).searchParams.get("date_from") === from,
      { timeout: 30_000 },
    ),
    rangeForm
      .getByRole("button", { name: "Show occurrences", exact: true })
      .click(),
  ]);
}

async function createSchedule(
  page: Page,
  input: {
    weekday: number;
    start: string;
    end: string;
    from: string;
    location: string;
  },
): Promise<void> {
  await page.getByRole("tab", { name: "Schedules", exact: true }).click();
  await page
    .getByRole("button", { name: "Create schedule", exact: true })
    .click();
  const form = titledForm(page, "create-schedule-title");
  await expect(form).toBeVisible();
  await selectByText(combo(form, "Class type"), "E2E_CYCLE");
  await selectByText(combo(form, "Trainer"), "E2E Trainer (E2ETRN)");
  for (const [index, label] of WEEKDAYS.entries()) {
    const box = form.getByRole("checkbox", { name: label, exact: true });
    if (index === input.weekday) await box.check();
    else if (await box.isChecked()) await box.uncheck();
  }
  await form.getByLabel("Start time", { exact: true }).fill(input.start);
  await form.getByLabel("End time", { exact: true }).fill(input.end);
  await form.getByLabel("Effective from", { exact: true }).fill(input.from);
  await form.getByLabel("Effective until", { exact: true }).fill(input.from);
  await form.getByLabel("Location", { exact: true }).fill(input.location);
  await form.getByLabel("Capacity", { exact: true }).fill("4");
  await runCommand(page, /\/class-schedules\/?$/, () =>
    form.getByRole("button", { name: "Create schedule", exact: true }).click(),
  );
  await expect(form).toBeHidden();
}

async function bookClass(page: Page, occurrence: RegExp): Promise<void> {
  await page.getByRole("tab", { name: "This week", exact: true }).click();
  await page.getByRole("button", { name: "Book a class", exact: true }).click();
  const form = titledForm(page, "book-class-title");
  await expect(form).toBeVisible();
  await selectByText(combo(form, "Which class time?"), occurrence.source);
  await pickMember(form, "E2Escan", /E2Escan Visitor/);
  await selectByText(combo(form, "Which class pack?"), "E2E_CLASS_PACK");
  await runCommand(page, /\/class-bookings\/?$/, () =>
    form
      .getByRole("button", { name: "Save booking", exact: true })
      .click(),
  );
  await expect(form).toBeHidden();
}

async function cancelClassBooking(
  page: Page,
  reason: string,
  status: number,
): Promise<void> {
  await page
    .getByRole("button", { name: "Cancel booking", exact: true })
    .click();
  const form = titledForm(page, "cancel-class-booking-title");
  await expect(form).toBeVisible();
  await form
    .getByLabel("Class booking cancellation reason", { exact: true })
    .fill(reason);
  const responsePromise = waitForCommand(
    page,
    /\/class-bookings\/[^/]+\/cancel\/?$/,
  );
  await form
    .getByRole("button", {
      name: "Confirm class booking cancellation",
      exact: true,
    })
    .click();
  const response = await responsePromise;
  const body = redactSecrets(await response.text());
  expect(response.status(), body.slice(0, 500)).toBe(status);
  if (status === 200) {
    expect(body).toContain(reason);
    await expectNotice(page, "Booking cancelled.");
  }
}

async function issueChallenge(page: Page): Promise<string> {
  const issued = page.waitForResponse((response) => {
    const status = response.status();
    if (status >= 300 && status < 400) return false;
    return (
      response.request().method() === "POST" &&
      /\/api\/operations\/check-in-challenges\/?$/.test(
        new URL(response.url()).pathname,
      )
    );
  });
  await page.goto("/check-ins/display");
  await expect(page).toHaveURL(/\/check-ins\?view=qr$/);
  await expect(
    page.getByRole("heading", { name: "Check-ins", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Onsite check-in code", exact: true }),
  ).toBeVisible();
  const response = await issued;
  const body = await response.text();
  expect(response.status(), redact(body)).toBe(201);
  const payload = readChallenge(body);
  const stored = await page.evaluate(() => {
    const blob = JSON.stringify({
      localStorage: Object.entries(localStorage),
      sessionStorage: Object.entries(sessionStorage),
      cookie: document.cookie,
    });
    return blob.includes("gms-checkin:v1:");
  });
  expect(stored).toBe(false);
  return payload;
}

function readChallenge(body: string): string {
  const parsed: unknown = JSON.parse(body);
  if (typeof parsed !== "object" || parsed === null || !("data" in parsed)) {
    throw new Error("The check-in challenge response omitted its data.");
  }
  const data = parsed.data;
  if (typeof data !== "object" || data === null || !("payload" in data)) {
    throw new Error(
      "The check-in challenge response omitted its one-time code.",
    );
  }
  const payload = data.payload;
  if (typeof payload !== "string" || !payload.startsWith("gms-checkin:v1:")) {
    throw new Error(
      "The check-in challenge response omitted its one-time code.",
    );
  }
  return payload;
}

async function readMemberId(page: Page, firstName: string): Promise<string> {
  const id = await page.evaluate(async (name) => {
    const response = await fetch(
      `/api/operations/members?search=${name}&status=ACTIVE&limit=25`,
      { credentials: "same-origin" },
    );
    const body: unknown = await response.json();
    const rows =
      typeof body === "object" &&
      body !== null &&
      "data" in body &&
      Array.isArray(body.data)
        ? body.data
        : [];
    const member = rows.find(
      (row: unknown) =>
        typeof row === "object" &&
        row !== null &&
        "first_name" in row &&
        row.first_name === name,
    ) as { id?: string } | undefined;
    return member?.id ?? "";
  }, firstName);
  expect(id).toMatch(/^[0-9a-f-]{36}$/i);
  return id;
}

async function scanUnassigned(
  page: Page,
  payload: string,
  memberId: string,
): Promise<{ status: number; body: string; checkIns: number }> {
  return page.evaluate(
    async ({ code, member }) => {
      const csrfResponse = await fetch("/api/auth/csrf", {
        credentials: "same-origin",
      });
      const csrfBody: unknown = await csrfResponse.json();
      const token =
        typeof csrfBody === "object" &&
        csrfBody !== null &&
        "data" in csrfBody &&
        typeof csrfBody.data === "object" &&
        csrfBody.data !== null &&
        "csrf_token" in csrfBody.data &&
        typeof csrfBody.data.csrf_token === "string"
          ? csrfBody.data.csrf_token
          : "";
      const scanResponse = await fetch(
        "/api/operations/trainer/check-ins/scan/",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": token,
          },
          body: JSON.stringify({ member_id: member, payload: code }),
        },
      );
      const scanText = await scanResponse.text();
      const listed = await fetch(
        `/api/operations/check-ins/?member_id=${member}&limit=10`,
        { credentials: "same-origin" },
      );
      const listedBody: unknown = await listed.json().catch(() => null);
      const checkIns =
        typeof listedBody === "object" &&
        listedBody !== null &&
        "data" in listedBody &&
        Array.isArray(listedBody.data)
          ? listedBody.data.length
          : 0;
      return {
        status: scanResponse.status,
        body: scanText
          .replace(/gms-checkin:v1:[A-Za-z0-9_-]+/g, "[check-in-code]")
          .slice(0, 500),
        checkIns,
      };
    },
    { code: payload, member: memberId },
  );
}

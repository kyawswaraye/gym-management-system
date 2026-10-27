import { expect, test, type Page } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

async function printCount(page: Page): Promise<number> {
  return page.evaluate(
    () => (window as Window & { __printCount?: number }).__printCount ?? 0,
  );
}

test("owner previews a draft and a test slip without printing", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const assertResponses = watchJsonResponses(page, [
    ownerAccount.password,
    ownerAccount.totpSecret,
  ]);
  const popups: string[] = [];
  page.on("popup", (popup) => {
    popups.push(popup.url());
  });
  await page.addInitScript(() => {
    const browser = window as unknown as Window & { __printCount: number };
    browser.__printCount = 0;
    window.print = () => {
      browser.__printCount += 1;
    };
  });

  await signIn(page, ownerAccount);
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Printer", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Printer settings", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/printable width at 72mm/)).toBeVisible();
  await expect(page.getByText(/do not scale the page to A4/)).toBeVisible();

  await page.getByRole("link", { name: "Test print", exact: true }).click();
  await expect(page).toHaveURL(/\/receipts\/test-print$/);
  await expect(
    page.getByText("TEST — not a receipt", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("TEST", { exact: true })).toBeVisible();
  await expect(page.getByText(/မလှ/)).toBeVisible();
  await expect(page.getByText(/အသင်းဝင်ကြေး/)).toBeVisible();
  await expect(page.locator("[data-auto-print='false']")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Print test slip" }),
  ).toBeVisible();
  expect(await printCount(page)).toBe(0);
  expect(popups).toEqual([]);

  await page.goto("/payments");
  await page
    .getByRole("button", { name: "Record payment", exact: true })
    .click();
  const form = page.getByRole("dialog", { name: "Record payment" });
  const description = `Printer preview ${crypto.randomUUID()}`;
  await form
    .getByRole("combobox", { name: "Purpose", exact: true })
    .selectOption("OTHER");
  await form
    .getByRole("textbox", { name: "Description", exact: true })
    .fill(description);
  await form
    .getByRole("textbox", { name: "Amount (THB)", exact: true })
    .fill("15.00");
  await form
    .getByRole("combobox", { name: "Method", exact: true })
    .selectOption("OTHER");
  await form.getByRole("button", { name: "Save draft", exact: true }).click();

  const detail = page.getByRole("region", { name: "Payment detail" });
  await detail.getByRole("button", { name: "Submit", exact: true }).click();
  const submit = page.getByRole("dialog", {
    name: "Submit this draft for approval?",
  });
  await expect(
    submit.getByText("DRAFT — not a receipt", { exact: true }),
  ).toBeVisible();
  await expect(submit.getByText("Assigned after approval")).toHaveCount(2);
  await expect(submit).not.toContainText(/RCPT-|RECEIPT-/);
  await expect(
    submit.getByRole("button", { name: "Print receipt" }),
  ).toHaveCount(0);
  await submit
    .getByRole("button", { name: "Confirm submit", exact: true })
    .click();
  await expect(
    detail.getByText("PENDING_APPROVAL", { exact: true }),
  ).toBeVisible();
  expect(await printCount(page)).toBe(0);
  expect(popups).toEqual([]);
  await expectNoBrowserSecrets(page, [
    ownerAccount.password,
    ownerAccount.totpSecret,
  ]);
  await assertResponses();
});

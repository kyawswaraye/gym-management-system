import { expect, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

const PAGES = [
  ["Payments", "/payments", "Payments"],
  ["Cash book", "/cash-book", "Cash book"],
  ["Expenses", "/expenses", "Expenses"],
  ["Salaries", "/salaries", "Salaries"],
  ["Inventory", "/inventory", "Inventory"],
  ["Retail sales", "/retail-sales", "Retail sales"],
] as const;

test("owner opens finance pages and submits an other payment draft", async ({
  page,
}) => {
  const assertResponses = watchJsonResponses(page, [
    ownerAccount.password,
    ownerAccount.totpSecret,
  ]);
  await signIn(page, ownerAccount);
  const nav = page.getByRole("navigation", { name: "Primary" });
  for (const [label, href] of PAGES) {
    await expect(
      nav.getByRole("link", { name: label, exact: true }),
    ).toHaveAttribute("href", href);
  }

  for (const [label, href, heading] of PAGES) {
    await page.goto(href);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: label, exact: true }),
    ).toBeVisible();
  }

  await page.goto("/payments");
  const form = page.locator("form").filter({
    has: page.getByRole("heading", { name: "Record payment", exact: true }),
  });
  const description = `E2E locker ${crypto.randomUUID()}`;
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
  await expect(
    detail.getByRole("heading", { name: "Payment detail", exact: true }),
  ).toBeVisible();
  await expect(detail.getByText(description, { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "Submit", exact: true }).click();
  await detail
    .getByRole("button", { name: "Confirm submit", exact: true })
    .click();
  await expect(
    detail.getByText("PENDING_APPROVAL", { exact: true }),
  ).toBeVisible();
  await expectNoBrowserSecrets(page, [
    ownerAccount.password,
    ownerAccount.totpSecret,
  ]);
  await assertResponses();
});

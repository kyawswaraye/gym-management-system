import { expect, type Locator, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  managerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

function fact(scope: Locator, label: string) {
  return scope.getByText(label, { exact: true }).locator("..");
}

test("manager returns one unit of a posted card sale", async ({ page }) => {
  const assertResponses = watchJsonResponses(page, [
    managerAccount.password,
    managerAccount.totpSecret,
  ]);
  await signIn(page, managerAccount);
  await page.goto("/retail-sales");
  await expect(
    page.getByRole("heading", { name: "Retail sales", exact: true }),
  ).toBeVisible();

  const sale = page.getByRole("region", { name: "Sale detail" });
  await page
    .getByRole("row", { name: /POSTED/ })
    .getByRole("button")
    .click();
  await expect(sale.getByText("E2E Bottle")).toBeVisible();
  await expect(sale.getByRole("listitem")).toContainText("sold 3.000");
  await expect(sale.getByRole("listitem")).toContainText("returned 0.000");
  await expect(sale.getByRole("listitem")).toContainText("remaining 3.000");
  await expect(sale.getByRole("button", { name: "Void sale" })).toBeVisible();

  await sale.getByRole("button", { name: "Partial return" }).click();
  await sale
    .getByRole("textbox", { name: "Return quantity for E2E Bottle" })
    .fill("1");
  await sale
    .getByRole("textbox", { name: "Return reason" })
    .fill("E2E damaged unit");
  await sale.getByRole("button", { name: "Confirm return" }).click();

  await expect(fact(sale, "Refund amount")).toContainText("10.00");
  await expect(fact(sale, "Refund payment")).not.toContainText("None");
  await expect(fact(sale, "Correction receipt")).not.toContainText("None");
  await expect(fact(sale, "Cash movement")).toContainText("None");
  await expect(fact(sale, "Stock returns")).toContainText("1");
  await expect(fact(sale, "Returned amount")).toContainText("10.00");
  await expect(fact(sale, "Returns")).toContainText("1");
  await expect(sale.getByRole("listitem")).toContainText("returned 1.000");
  await expect(sale.getByRole("listitem")).toContainText("remaining 2.000");
  await expect(sale.getByRole("button", { name: "Void sale" })).toHaveCount(0);
  await expect(
    sale.getByRole("button", { name: "Partial return" }),
  ).toBeVisible();
  await expectNoBrowserSecrets(page, [
    managerAccount.password,
    managerAccount.totpSecret,
  ]);
  await assertResponses();
});

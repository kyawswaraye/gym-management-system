import { expect, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  platformAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

test("super admin provisions a gym through the browser", async ({ page }) => {
  const assertResponses = watchJsonResponses(page);
  const secrets = [platformAccount.password, platformAccount.totpSecret];

  await signIn(page, platformAccount);
  await expect(page).toHaveURL(/\/super-admin\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "GymPilot MM", exact: true }),
  ).toBeVisible();
  await expectNoBrowserSecrets(page, secrets);

  await page.getByRole("link", { name: "Gyms", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Gyms", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Provision gym", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Provision gym", exact: true }),
  ).toBeVisible();

  await page
    .getByLabel("Gym name *", { exact: true })
    .fill("E2E Provision Gym");
  await page
    .getByRole("combobox", { name: "Subscription *", exact: true })
    .selectOption({ label: "Trial (THB)" });
  await page.getByLabel("Timezone *", { exact: true }).fill("Asia/Yangon");
  await page
    .getByRole("combobox", { name: "Region", exact: true })
    .selectOption("Yangon Region");
  await page
    .getByRole("combobox", { name: "City", exact: true })
    .selectOption("Yangon");
  await page.getByLabel("Country code", { exact: true }).fill("MM");
  await page.getByLabel("Owner first name *", { exact: true }).fill("E2E");
  await page.getByLabel("Owner last name *", { exact: true }).fill("Provision");
  await page
    .getByLabel("Owner email *", { exact: true })
    .fill("e2e-prov-owner@example.test");
  await page
    .getByRole("button", { name: "Provision gym and email Owner", exact: true })
    .click();

  await expect(page.getByRole("status")).toHaveText(
    "E2E-PROVISION-GYM was provisioned and an Owner setup email was sent.",
  );
  const listed = page.getByRole("link", {
    name: "E2E-PROVISION-GYM",
    exact: true,
  });
  await expect(listed).toBeVisible();
  await page
    .getByRole("textbox", { name: "Search gyms", exact: true })
    .fill("E2E-PROVISION-GYM");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(listed).toBeVisible();
  await listed.click();

  await expect(page).toHaveURL(/\/super-admin\/gyms\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { name: "E2E Provision Gym", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("E2E-PROVISION-GYM · ACTIVE", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("GYM_PROVISIONED", { exact: true }),
  ).toBeVisible();
  await expectNoBrowserSecrets(page, secrets);
  await assertResponses();
});

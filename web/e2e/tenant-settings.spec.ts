import { expect, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

test("owner updates gym settings through the browser", async ({ page }) => {
  const assertResponses = watchJsonResponses(page);
  const secrets = [ownerAccount.password, ownerAccount.totpSecret];

  await signIn(page, ownerAccount);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Owner dashboard", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("E2E Seed Gym", { exact: true })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "Primary" });
  await nav.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    nav.getByRole("link", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    nav.getByRole("link", { name: "Owners", exact: true }),
  ).toBeVisible();
  await expect(
    nav.getByRole("link", { name: "Staff", exact: true }),
  ).toBeVisible();
  await expect(
    nav.getByRole("link", { name: "Super admin", exact: true }),
  ).toHaveCount(0);

  await page.goto("/super-admin/gyms");
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Owner dashboard", exact: true }),
  ).toBeVisible();

  await nav.getByRole("button", { name: "Settings", exact: true }).click();
  await nav.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(
    page.getByRole("heading", { name: "Gym settings", exact: true }),
  ).toBeVisible();

  const profileTab = page.getByRole("tab", { name: "Profile", exact: true });
  const operationsTab = page.getByRole("tab", {
    name: "Operations",
    exact: true,
  });

  await profileTab.click();
  await page
    .getByRole("button", { name: "Edit gym profile", exact: true })
    .click();
  const name = page.getByLabel("Name", { exact: true });
  const timezone = page.getByLabel("Timezone", { exact: true });
  const currency = page.getByLabel("Currency", { exact: true });
  await expect(name).toHaveValue("E2E Seed Gym");
  await expect(timezone).toHaveValue("Asia/Yangon");
  await expect(currency).toHaveValue("THB");
  await expect(
    page.getByRole("button", { name: "Save gym profile", exact: true }),
  ).toBeVisible();

  await operationsTab.click();
  await page
    .getByRole("button", { name: "Edit operational settings", exact: true })
    .click();
  const week = page.getByRole("combobox", {
    name: "Week starts on",
    exact: true,
  });
  const memberPrefix = page.getByLabel("Member number prefix", { exact: true });
  const receiptPrefix = page.getByLabel("Receipt number prefix", {
    exact: true,
  });
  const duplicateWindow = page.getByLabel(
    "Check-in duplicate window (seconds)",
    {
      exact: true,
    },
  );
  const warningDays = page.getByLabel("Membership expiry warning days", {
    exact: true,
  });
  const manualNumbers = page.getByRole("checkbox", {
    name: "Allow manual member numbers",
    exact: true,
  });
  await expect(week).toHaveValue("1");
  await expect(memberPrefix).toHaveValue("MEM");
  await expect(receiptPrefix).toHaveValue("RCPT");
  await expect(duplicateWindow).toHaveValue("300");
  await expect(warningDays).toHaveValue("0, 3, 7, 30");
  await expect(manualNumbers).not.toBeChecked();
  await expect(
    page.getByRole("button", {
      name: "Save operational settings",
      exact: true,
    }),
  ).toBeVisible();

  await profileTab.click();
  await name.fill("E2E Seed Gym East");
  await timezone.fill("Asia/Bangkok");
  await currency.fill("USD");
  await page
    .getByRole("button", { name: "Save gym profile", exact: true })
    .click();
  await expect(page.getByRole("status")).toHaveText("Gym profile saved.");

  await operationsTab.click();
  await week.selectOption({ label: "Sunday" });
  await manualNumbers.check();
  await memberPrefix.fill("E2EMEM");
  await receiptPrefix.fill("E2ERC");
  await duplicateWindow.fill("240");
  await warningDays.fill("1, 7, 14");
  await page
    .getByRole("button", { name: "Save operational settings", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Operational settings saved." }),
  ).toBeVisible();

  await page.reload();
  await profileTab.click();
  await page
    .getByRole("button", { name: "Edit gym profile", exact: true })
    .click();
  await expect(name).toHaveValue("E2E Seed Gym East");
  await expect(timezone).toHaveValue("Asia/Bangkok");
  await expect(currency).toHaveValue("USD");

  await operationsTab.click();
  await page
    .getByRole("button", { name: "Edit operational settings", exact: true })
    .click();
  await expect(week).toHaveValue("0");
  await expect(manualNumbers).toBeChecked();
  await expect(memberPrefix).toHaveValue("E2EMEM");
  await expect(receiptPrefix).toHaveValue("E2ERC");
  await expect(duplicateWindow).toHaveValue("240");
  await expect(warningDays).toHaveValue("1, 7, 14");
  await expectNoBrowserSecrets(page, secrets);
  await assertResponses();
});

import { expect, type Locator, type Page, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";
import {
  assertProfilePhotoFixtures,
  type ProfilePhotoFixture,
  profilePhotos,
} from "./profile-photos";

const ORIGINAL_FIRST = "E2Ephase";
const UPDATED_FIRST = "Updatedphase";
const LAST_NAME = "Lifecycle";
const ORIGINAL_NAME = `${ORIGINAL_FIRST} ${LAST_NAME}`;
const UPDATED_NAME = `${UPDATED_FIRST} ${LAST_NAME}`;
const PLAN_CODE = "E2E_ONE_DAY";
const PLAN_NAME = "E2E One Day";
const REVISED_PLAN = "E2E One Day Revised";
const OPENING_NOTE = "E2E opening note";
const REVISED_NOTE = "E2E revised note";
const PLAN_DESCRIPTION = "E2E revised plan";
const EMAIL = "e2e-phase2-member@example.test";
const PHONE = "+959123450001";
const UPDATED_PHONE = "+959123450002";
const PHOTO_TIMEOUT = 60_000;

test.use({ actionTimeout: 15_000 });

function combobox(scope: Locator | Page, name: string): Locator {
  return scope.getByRole("combobox", { name, exact: true });
}

function settledResponse(response: { status: () => number }): boolean {
  const status = response.status();
  return status < 300 || status >= 400;
}

function objectOrigin(): string {
  const value = process.env.NEXT_PUBLIC_OBJECT_ORIGINS?.trim() ?? "";
  if (!value) {
    throw new Error(
      "NEXT_PUBLIC_OBJECT_ORIGINS is required for profile photo E2E.",
    );
  }
  return value;
}

function storageSecrets(): string[] {
  const secrets = [ownerAccount.password, ownerAccount.totpSecret];
  for (const name of ["MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"]) {
    const value = process.env[name]?.trim() ?? "";
    if (value && !secrets.includes(value)) secrets.push(value);
  }
  return secrets;
}

function nextIsoDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match?.[1] || !match[2] || !match[3]) {
    throw new Error(`Unexpected date ${iso}.`);
  }
  const date = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  );
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

function challengePayload(body: string): string {
  const payload = responseBodyData(body).payload;
  if (typeof payload !== "string" || !payload.startsWith("gms-checkin:v1:")) {
    throw new Error(
      "The check-in challenge response omitted its one-time code.",
    );
  }
  return payload;
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
  await expect
    .poll(() =>
      image.evaluate((element: HTMLImageElement) => element.naturalWidth),
    )
    .toBeGreaterThan(0);
  const src = (await image.getAttribute("src")) ?? "";
  expect(src.startsWith("data:image/")).toBe(true);
  const text = await page.locator("body").innerText();
  const storage = await page.evaluate(() =>
    JSON.stringify({
      localStorage: Object.entries(localStorage),
      sessionStorage: Object.entries(sessionStorage),
    }),
  );
  expect(text.includes(payload)).toBe(false);
  expect(text.includes("gms-checkin:v1:")).toBe(false);
  expect(src.includes(payload)).toBe(false);
  expect(storage.includes(payload)).toBe(false);
  expect(storage.includes("gms-checkin:v1:")).toBe(false);
}

function responseBodyData(body: string): Record<string, unknown> {
  const payload: unknown = JSON.parse(body);
  if (typeof payload !== "object" || payload === null || !("data" in payload)) {
    throw new Error(`Unexpected response body: ${body}`);
  }
  const data = payload.data;
  if (typeof data !== "object" || data === null) {
    throw new Error(`Unexpected response data: ${body}`);
  }
  return data as Record<string, unknown>;
}

async function responseData(
  response: Awaited<ReturnType<Page["waitForResponse"]>>,
): Promise<Record<string, unknown>> {
  const payload: unknown = await response.json();
  if (typeof payload !== "object" || payload === null || !("data" in payload)) {
    throw new Error(`Unexpected response from ${response.url()}.`);
  }
  const data = payload.data;
  if (typeof data !== "object" || data === null) {
    throw new Error(`Unexpected response data from ${response.url()}.`);
  }
  return data as Record<string, unknown>;
}

async function openStartMembershipDialog(page: Page): Promise<Locator> {
  await page
    .getByRole("button", { name: "Open start membership form", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Start membership",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function openSection(
  page: Page,
  name: "Members" | "Memberships" | "Check-ins",
): Promise<void> {
  const nav = page.getByRole("navigation", { name: "Primary" });
  if (name === "Members" || name === "Memberships") {
    const folder = nav.getByRole("button", { name: "Members", exact: true });
    if ((await folder.getAttribute("aria-expanded")) !== "true") {
      await folder.click();
    }
  }
  await nav.getByRole("link", { name, exact: true }).click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
}

async function settleMembers(
  page: Page,
  action: () => Promise<unknown>,
  query: Record<string, string>,
): Promise<void> {
  const pending = page.waitForResponse(
    (response) => {
      if (response.request().method() !== "GET") return false;
      const url = new URL(response.url());
      if (url.pathname !== "/api/operations/members") return false;
      return Object.entries(query).every(
        ([key, value]) => url.searchParams.get(key) === value,
      );
    },
    { timeout: 15_000 },
  );
  await action();
  const response = await pending;
  expect(response.status()).toBeLessThan(500);
  await expect(page.getByText("Loading members.", { exact: true })).toHaveCount(
    0,
  );
}

function memberRow(page: Page, name: string): Locator {
  return page.getByRole("row").filter({
    has: page.getByRole("link", { name, exact: true }),
  });
}

const MEMBER_STATUS_LABEL = {
  "": "All",
  ACTIVE: "Active",
  SUSPENDED: "Suspended",
  ARCHIVED: "Archived",
} as const;

function memberStatusOption(
  page: Page,
  status: keyof typeof MEMBER_STATUS_LABEL,
): Locator {
  return page.getByRole("radio", {
    name: MEMBER_STATUS_LABEL[status],
    exact: true,
  });
}

async function fillProfile(
  scope: Locator,
  values: {
    firstName: string;
    lastName: string;
    phone: string;
    email: string;
    gender: string;
    birth: string;
    line1: string;
    line2: string;
    city: string;
    region: string;
    postal: string;
    country: string;
    emergencyName: string;
    emergencyPhone: string;
    notes: string;
  },
): Promise<void> {
  await scope.getByLabel("First name", { exact: true }).fill(values.firstName);
  await scope.getByLabel("Last name", { exact: true }).fill(values.lastName);
  await scope.getByLabel("Phone", { exact: true }).fill(values.phone);
  await scope.getByLabel("Email", { exact: true }).fill(values.email);
  await combobox(scope, "Gender").selectOption({ label: values.gender });
  await scope.getByLabel("Date of birth", { exact: true }).fill(values.birth);
  await scope.getByLabel("Address line 1", { exact: true }).fill(values.line1);
  await scope.getByLabel("Address line 2", { exact: true }).fill(values.line2);
  await scope.getByLabel("City", { exact: true }).fill(values.city);
  await scope.getByLabel("Region", { exact: true }).fill(values.region);
  await scope.getByLabel("Postal code", { exact: true }).fill(values.postal);
  await scope.getByLabel("Country code", { exact: true }).fill(values.country);
  await scope
    .getByLabel("Emergency contact name", { exact: true })
    .fill(values.emergencyName);
  await scope
    .getByLabel("Emergency contact phone", { exact: true })
    .fill(values.emergencyPhone);
  await scope
    .getByRole("textbox", { name: "Notes", exact: true })
    .fill(values.notes);
}

async function expectNoAlert(page: Page): Promise<void> {
  // Next.js keeps an empty route announcer with role=alert. App errors use p.error.
  await expect(page.locator("p.error[role='alert']")).toHaveCount(0);
}

test("owner runs the member lifecycle, front-desk check-in, and rotating QR", async ({
  page,
}) => {
  test.setTimeout(300_000);
  assertProfilePhotoFixtures();
  const secrets = storageSecrets();
  const assertResponses = watchJsonResponses(page, secrets);
  const origin = objectOrigin();

  await signIn(page, ownerAccount);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Owner dashboard", exact: true }),
  ).toBeVisible();

  await openSection(page, "Members");
  await expect(page.getByText("Loading members.", { exact: true })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "Add member", exact: true }).click();
  const addMember = page.locator("form").filter({
    has: page.getByRole("heading", { name: "Add member", exact: true }),
  });
  await expect(addMember.getByLabel("Join date", { exact: true })).toHaveValue(
    /^\d{4}-\d{2}-\d{2}$/,
  );
  const joinDate = await addMember
    .getByLabel("Join date", { exact: true })
    .inputValue();

  await test.step("create a member with the published profile", async () => {
    await fillProfile(addMember, {
      firstName: ORIGINAL_FIRST,
      lastName: LAST_NAME,
      phone: PHONE,
      email: "E2E-Phase2-Member@Example.test",
      gender: "Female",
      birth: "1990-04-12",
      line1: "18 E2E Lane",
      line2: "Floor 2",
      city: "Yangon",
      region: "Yangon Region",
      postal: "11181",
      country: "mm",
      emergencyName: "E2E Kin",
      emergencyPhone: "+959123450099",
      notes: "E2E intake note",
    });
    await addMember
      .getByRole("button", { name: "Save member", exact: true })
      .click();
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();
    await expectNoAlert(page);
  });

  const createdRow = memberRow(page, ORIGINAL_NAME);
  const memberCellText = await createdRow.getByRole("cell").first().innerText();
  const memberNumber = memberCellText.match(/\bMEM-\d+\b/)?.[0] ?? "";
  expect(memberNumber).not.toBe("");
  await expect(createdRow).toContainText(PHONE);
  await expect(createdRow).toContainText("ACTIVE");
  await expect(createdRow).toContainText(joinDate);

  await test.step("search and filter the new member", async () => {
    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(ORIGINAL_FIRST),
      {
        search: ORIGINAL_FIRST,
        limit: "25",
      },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();

    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(memberNumber),
      { search: memberNumber, limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();

    await settleMembers(
      page,
      () => memberStatusOption(page, "ACTIVE").click(),
      { status: "ACTIVE", search: memberNumber, limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();

    await settleMembers(
      page,
      () => memberStatusOption(page, "SUSPENDED").click(),
      { status: "SUSPENDED", search: memberNumber, limit: "25" },
    );
    await expect(
      page.getByText("No members found.", { exact: true }),
    ).toBeVisible();

    await settleMembers(page, () => memberStatusOption(page, "").click(), {
      search: memberNumber,
      limit: "25",
    });
    await settleMembers(
      page,
      () => page.getByLabel("Search members", { exact: true }).fill(""),
      { limit: "25" },
    );

    await settleMembers(
      page,
      () => page.getByLabel("Joined from", { exact: true }).fill(joinDate),
      { joined_from: joinDate, limit: "25" },
    );
    await settleMembers(
      page,
      () => page.getByLabel("Joined to", { exact: true }).fill(joinDate),
      { joined_from: joinDate, joined_to: joinDate, limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();

    const later = nextIsoDate(joinDate);
    await settleMembers(
      page,
      () => page.getByLabel("Joined from", { exact: true }).fill(later),
      { joined_from: later, limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toHaveCount(0);

    await settleMembers(
      page,
      () => page.getByLabel("Joined from", { exact: true }).fill(""),
      { limit: "25" },
    );
    await settleMembers(
      page,
      () =>
        page
          .getByRole("combobox", { name: "Filter by PT client", exact: true })
          .selectOption("true"),
      { is_pt_client: "true", limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toHaveCount(0);
    await settleMembers(
      page,
      () =>
        page
          .getByRole("combobox", { name: "Filter by PT client", exact: true })
          .selectOption(""),
      { limit: "25" },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toBeVisible();
  });

  await memberRow(page, ORIGINAL_NAME)
    .getByRole("link", { name: ORIGINAL_NAME, exact: true })
    .click();
  await expect(page).toHaveURL(/\/members\/[0-9a-f-]+$/);
  await page.getByRole("button", { name: "Edit profile", exact: true }).click();
  const profile = page.getByRole("dialog", { name: "Edit profile" });
  await expect(profile.getByLabel("First name", { exact: true })).toHaveValue(
    ORIGINAL_FIRST,
  );
  await expect(profile.getByLabel("Last name", { exact: true })).toHaveValue(
    LAST_NAME,
  );
  await expect(profile.getByLabel("Phone", { exact: true })).toHaveValue(PHONE);
  await expect(profile.getByLabel("Email", { exact: true })).toHaveValue(EMAIL);
  await expect(combobox(profile, "Gender")).toHaveValue("FEMALE");
  await expect(
    profile.getByLabel("Date of birth", { exact: true }),
  ).toHaveValue("1990-04-12");
  await expect(
    profile.getByLabel("Address line 1", { exact: true }),
  ).toHaveValue("18 E2E Lane");
  await expect(
    profile.getByLabel("Address line 2", { exact: true }),
  ).toHaveValue("Floor 2");
  await expect(profile.getByLabel("City", { exact: true })).toHaveValue(
    "Yangon",
  );
  await expect(profile.getByLabel("Region", { exact: true })).toHaveValue(
    "Yangon Region",
  );
  await expect(profile.getByLabel("Postal code", { exact: true })).toHaveValue(
    "11181",
  );
  await expect(profile.getByLabel("Country code", { exact: true })).toHaveValue(
    "MM",
  );
  await expect(profile.getByLabel("Join date", { exact: true })).toHaveValue(
    joinDate,
  );
  await expect(
    profile.getByLabel("Emergency contact name", { exact: true }),
  ).toHaveValue("E2E Kin");
  await expect(
    profile.getByLabel("Emergency contact phone", { exact: true }),
  ).toHaveValue("+959123450099");
  await expect(
    profile.getByRole("textbox", { name: "Notes", exact: true }),
  ).toHaveValue("E2E intake note");
  await expect(profile.locator("p.muted")).toHaveText(memberNumber);
  await expect(
    page.getByText("No profile photo.", { exact: true }),
  ).toBeVisible();

  const uploadedUrls: string[] = [];
  async function uploadPhoto(
    label: string,
    fixture: ProfilePhotoFixture,
  ): Promise<void> {
    const browserOrigin = new URL(page.url()).origin;
    const posted = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        response.url().startsWith(`${origin}/`),
      { timeout: PHOTO_TIMEOUT },
    );
    const completed = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/api\/operations\/files\/[0-9a-f-]+\/complete\/?$/.test(
          new URL(response.url()).pathname,
        ),
      { timeout: PHOTO_TIMEOUT },
    );
    const displayed = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "GET" &&
        response.request().resourceType() === "image" &&
        response.url().startsWith(`${origin}/`),
      { timeout: PHOTO_TIMEOUT },
    );
    await page.getByLabel(label, { exact: true }).setInputFiles({
      name: fixture.fileName,
      mimeType: fixture.contentType,
      buffer: fixture.bytes,
    });
    const upload = await posted;
    expect(upload.status()).toBe(204);
    expect((upload.headers()["access-control-allow-origin"] ?? "").trim()).toBe(
      browserOrigin,
    );
    const completion = await completed;
    const completionBody = await completion.text();
    expect(completion.status(), completionBody).toBeLessThan(400);
    expect(responseBodyData(completionBody).attachable).toBe(true);
    await expect(
      page.getByText("Profile photo saved.", { exact: true }),
    ).toBeVisible({
      timeout: PHOTO_TIMEOUT,
    });
    const image = page.getByRole("img", {
      name: `Profile photo of ${ORIGINAL_NAME}`,
      exact: true,
    });
    await expect(image).toBeVisible();
    const fetched = await displayed;
    expect(fetched.ok()).toBeTruthy();
    expect(fetched.headers()["content-type"] ?? "").toContain("image/");
    const src = await image.getAttribute("src");
    expect(src?.startsWith(`${origin}/`)).toBeTruthy();
    expect(src ?? "").not.toContain("@");
    for (const secret of secrets) {
      expect((src ?? "").toLowerCase()).not.toContain(secret.toLowerCase());
    }
    await expect
      .poll(
        () =>
          image.evaluate((element: HTMLImageElement) => element.naturalWidth),
        {
          timeout: PHOTO_TIMEOUT,
        },
      )
      .toBeGreaterThan(0);
    uploadedUrls.push(src ?? "");
    await expectNoAlert(page);
    await expect(
      page.getByText(
        "The previous photo could not be removed and will be deleted later.",
        {
          exact: true,
        },
      ),
    ).toHaveCount(0);
  }

  await test.step("upload, replace, and remove a live profile photo", async () => {
    await uploadPhoto("Upload profile photo", profilePhotos.png);
    await uploadPhoto("Replace profile photo", profilePhotos.jpeg);
    await uploadPhoto("Replace profile photo", profilePhotos.webp);
    expect(new Set(uploadedUrls).size).toBe(uploadedUrls.length);
    const html = await page.content();
    for (const secret of secrets) {
      expect(html.toLowerCase()).not.toContain(secret.toLowerCase());
    }
    await page
      .getByRole("button", { name: "Remove profile photo", exact: true })
      .click();
    await expect(
      page.getByText("Profile photo removed.", { exact: true }),
    ).toBeVisible({
      timeout: PHOTO_TIMEOUT,
    });
    await expect(
      page.getByText("No profile photo.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("img", {
        name: `Profile photo of ${ORIGINAL_NAME}`,
        exact: true,
      }),
    ).toHaveCount(0);
    await expectNoAlert(page);
  });

  await test.step("update the profile and confirm search follows it", async () => {
    await profile.getByLabel("First name", { exact: true }).fill(UPDATED_FIRST);
    await profile.getByLabel("Phone", { exact: true }).fill(UPDATED_PHONE);
    await combobox(profile, "Gender").selectOption({ label: "Male" });
    await profile.getByLabel("City", { exact: true }).fill("Mandalay");
    await profile
      .getByRole("textbox", { name: "Notes", exact: true })
      .fill("E2E updated lifecycle note");
    const saved = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "PATCH" &&
        /\/api\/operations\/members\/[0-9a-f-]+\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await profile
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    const response = await saved;
    expect(response.ok()).toBeTruthy();
    const data = await responseData(response);
    expect(data.first_name).toBe(UPDATED_FIRST);
    expect(data.last_name).toBe(LAST_NAME);
    await expect(
      page.getByRole("button", { name: "Edit profile", exact: true }),
    ).toBeVisible();
    await page.reload();
    await page
      .getByRole("button", { name: "Edit profile", exact: true })
      .click();
    await expect(profile.getByLabel("First name", { exact: true })).toHaveValue(
      UPDATED_FIRST,
    );
    await expect(profile.getByLabel("Phone", { exact: true })).toHaveValue(
      UPDATED_PHONE,
    );
    await expect(combobox(profile, "Gender")).toHaveValue("MALE");
    await expect(profile.getByLabel("City", { exact: true })).toHaveValue(
      "Mandalay",
    );
    await expect(
      profile.getByRole("textbox", { name: "Notes", exact: true }),
    ).toHaveValue("E2E updated lifecycle note");
    await expect(profile.getByLabel("Last name", { exact: true })).toHaveValue(
      LAST_NAME,
    );

    await openSection(page, "Members");
    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(ORIGINAL_FIRST),
      {
        search: ORIGINAL_FIRST,
        limit: "25",
      },
    );
    await expect(memberRow(page, ORIGINAL_NAME)).toHaveCount(0);
    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(UPDATED_FIRST),
      { search: UPDATED_FIRST, limit: "25" },
    );
    await expect(memberRow(page, UPDATED_NAME)).toBeVisible();
    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(UPDATED_PHONE),
      { search: UPDATED_PHONE, limit: "25" },
    );
    await expect(memberRow(page, UPDATED_NAME)).toContainText(UPDATED_PHONE);
  });

  await openSection(page, "Memberships");
  await expect(
    page.getByText("Loading membership plans.", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Loading memberships.", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "New plan", exact: true }).click();
  const newPlan = page.locator("form").filter({
    has: page.getByRole("heading", { name: "New plan", exact: true }),
  });
  await expect(newPlan.getByLabel("Currency", { exact: true })).not.toHaveValue(
    "",
  );
  const currency = await newPlan
    .getByLabel("Currency", { exact: true })
    .inputValue();
  const plans = page.getByRole("table", { name: "Membership plans" });
  const memberships = page.getByRole("table", { name: "Memberships" });

  await test.step("create and patch a one-day plan", async () => {
    await newPlan.getByLabel("Name", { exact: true }).fill(PLAN_NAME);
    await newPlan.getByLabel("Duration", { exact: true }).fill("1");
    await newPlan
      .getByRole("combobox", { name: "Unit", exact: true })
      .selectOption({ label: "Day" });
    await newPlan.getByLabel("Price", { exact: true }).fill("1500.00");
    await newPlan.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("button", { name: "View plans", exact: true }).click();
    const createdPlan = plans.getByRole("row").filter({ hasText: PLAN_CODE });
    await expect(createdPlan).toContainText(PLAN_NAME);
    await expect(createdPlan).toContainText("1 Day");
    await expect(createdPlan).toContainText(`1500.00 ${currency}`);
    await expect(createdPlan).toContainText("Active");

    await createdPlan
      .getByRole("button", {
        name: `Edit ${PLAN_NAME} (${PLAN_CODE}) plan`,
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("combobox", { name: "Unit", exact: true }),
    ).toHaveValue("DAY");
    await dialog.getByLabel("Name", { exact: true }).fill(REVISED_PLAN);
    await dialog
      .getByLabel("Description", { exact: true })
      .fill(PLAN_DESCRIPTION);
    await dialog.getByLabel("Price", { exact: true }).fill("1600.00");
    const patched = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "PATCH" &&
        /\/api\/operations\/membership-plans\/[0-9a-f-]+\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await dialog
      .getByRole("button", {
        name: "Save",
        exact: true,
      })
      .click();
    const response = await patched;
    expect(response.ok()).toBeTruthy();
    const data = await responseData(response);
    expect(data.name).toBe(REVISED_PLAN);
    expect(data.description).toBe(PLAN_DESCRIPTION);
    expect(data.price).toBe("1600.00");
    expect(data.duration_unit).toBe("DAY");
    await expect(dialog).toHaveCount(0);
    await expect(createdPlan).toContainText(REVISED_PLAN);
    await expect(createdPlan).toContainText(`1600.00 ${currency}`);
    await expect(createdPlan).toContainText("Active");
  });

  let startDate = "";
  await test.step("sell the plan and edit its note", async () => {
    const startForm = await openStartMembershipDialog(page);
    await startForm
      .getByLabel("Search members", { exact: true })
      .fill(UPDATED_FIRST);
    const matches = startForm.getByRole("list", { name: "Matching members" });
    const choice = matches.getByRole("button", {
      name: new RegExp(`${memberNumber} — ${UPDATED_NAME}`),
    });
    await expect(choice).toBeVisible();
    await choice.click();
    await expect(startForm).toContainText(
      `Selected member: ${memberNumber} — ${UPDATED_NAME}`,
    );
    const planSelect = startForm.getByRole("combobox", {
      name: "Plan",
      exact: true,
    });
    const option = planSelect.locator("option", { hasText: REVISED_PLAN });
    await expect(option).toHaveCount(1);
    const planId = await option.getAttribute("value");
    if (!planId) throw new Error("Revised plan is not for sale.");
    await planSelect.selectOption(planId);
    startDate = await startForm
      .getByLabel("Start date", { exact: true })
      .inputValue();
    expect(startDate).toBe(joinDate);
    await startForm.getByLabel("Note", { exact: true }).fill(OPENING_NOTE);
    await startForm
      .getByRole("button", { name: "Start membership", exact: true })
      .click();
    const sold = memberships.getByRole("row").filter({ hasText: REVISED_PLAN });
    await expect(sold).toContainText(`${startDate} – ${startDate}`);
    await expect(sold).toContainText("ACTIVE / UNPAID");
    await expect(sold).toContainText(`1600.00 ${currency}`);
    await expect(sold).toContainText(OPENING_NOTE);

    const expiry = page.waitForResponse((response) => {
      if (response.request().method() !== "GET") return false;
      const url = new URL(response.url());
      return (
        url.pathname === "/api/operations/memberships/expiry" &&
        url.searchParams.get("bucket") === "today"
      );
    });
    await page
      .getByRole("combobox", { name: "Membership list", exact: true })
      .selectOption({ label: "Expiring today" });
    expect((await expiry).ok()).toBeTruthy();
    await expect(sold).toBeVisible();
    const restore = page.waitForResponse((response) => {
      if (response.request().method() !== "GET") return false;
      const url = new URL(response.url());
      return (
        url.pathname === "/api/operations/memberships" &&
        !url.searchParams.has("bucket")
      );
    });
    await page
      .getByRole("combobox", { name: "Membership list", exact: true })
      .selectOption({ label: "All memberships" });
    expect((await restore).ok()).toBeTruthy();

    await sold.getByRole("button", { name: /Edit note for / }).click();
    const noteDialog = page.getByRole("dialog");
    await noteDialog
      .getByRole("textbox", { name: "Membership note", exact: true })
      .fill(REVISED_NOTE);
    const noted = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "PATCH" &&
        /\/api\/operations\/memberships\/[0-9a-f-]+\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await noteDialog.getByRole("button", { name: /Save note for / }).click();
    const noteResponse = await noted;
    expect(noteResponse.ok()).toBeTruthy();
    expect((await responseData(noteResponse)).note).toBe(REVISED_NOTE);
    await expect(sold).toContainText(REVISED_NOTE);
  });

  await test.step("check the eligible member in and out", async () => {
    await openSection(page, "Check-ins");
    await expect(
      page.getByText("Loading current visits.", { exact: true }),
    ).toHaveCount(0);
    await page
      .getByLabel("Search active members", { exact: true })
      .fill(memberNumber);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page
      .getByRole("button", {
        name: `Select ${UPDATED_NAME} (${memberNumber})`,
        exact: true,
      })
      .click();
    await expect(
      page.getByText("No check-ins yet.", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Check in", exact: true }).click();
    const checkIn = page.getByRole("dialog", { name: "Check in" });
    await checkIn.getByLabel("Note", { exact: true }).fill("E2E desk visit");
    const checkedIn = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/api\/operations\/check-ins\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await checkIn
      .getByRole("button", { name: "Check in", exact: true })
      .click();
    expect((await checkedIn).ok()).toBeTruthy();
    await expect(page.getByText("Checked in.", { exact: true })).toBeVisible();
    const openVisits = page.getByRole("region", {
      name: "Currently checked in",
    });
    await expect(openVisits).toContainText(
      `${UPDATED_NAME} (${memberNumber}) · Open`,
    );
    await expect(openVisits).toContainText("E2E desk visit");

    const checkedOut = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/api\/operations\/check-ins\/[0-9a-f-]+\/check-out\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await page
      .getByRole("button", {
        name: `Check out ${UPDATED_NAME} (${memberNumber})`,
        exact: true,
      })
      .click();
    expect((await checkedOut).ok()).toBeTruthy();
    await expect(page.getByText("Checked out.", { exact: true })).toBeVisible();
    await expect(openVisits).toContainText("Nobody is checked in.");
    const recent = page.getByRole("region", {
      name: `${UPDATED_NAME} (${memberNumber})`,
    });
    await expect(recent).toContainText(
      `${UPDATED_NAME} (${memberNumber}) · Checked out`,
    );
    await expect(recent).toContainText("E2E desk visit");
    await expectNoAlert(page);
  });

  await test.step("show the active summary, then suspend, resume, and cancel", async () => {
    await openSection(page, "Members");
    await memberRow(page, UPDATED_NAME)
      .getByRole("link", { name: UPDATED_NAME, exact: true })
      .click();
    const current = page.getByRole("region", { name: "Current membership" });
    await expect(current).toContainText(
      `${REVISED_PLAN}: ${startDate} – ${startDate} (UNPAID)`,
    );
    await expect(current).toContainText("1 day remaining");
    const attendance = page.getByRole("region", { name: "Check-ins" });
    await expect(attendance).toContainText("Not checked in");
    await expect(attendance).not.toContainText("Not recorded");
    await expect(
      attendance.getByRole("link", { name: "Open front desk", exact: true }),
    ).toBeVisible();

    await openSection(page, "Memberships");
    const sold = memberships.getByRole("row").filter({ hasText: REVISED_PLAN });
    await sold.getByRole("button", { name: "Suspend", exact: true }).click();
    await expect(sold).toContainText("SUSPENDED / UNPAID");
    await sold.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(sold).toContainText("ACTIVE / UNPAID");
    await sold.getByRole("button", { name: "Cancel", exact: true }).click();
    const cancelDialog = page.getByRole("dialog", {
      name: "Cancel this membership?",
    });
    await cancelDialog
      .getByLabel("Cancellation reason", { exact: true })
      .fill("E2E cancellation reason");
    const cancelled = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/cancel\/?$/.test(new URL(response.url()).pathname),
    );
    await cancelDialog
      .getByRole("button", { name: "Cancel membership", exact: true })
      .click();
    const cancelResponse = await cancelled;
    expect(cancelResponse.ok()).toBeTruthy();
    const cancellation = await responseData(cancelResponse);
    expect(cancellation.status).toBe("CANCELLED");
    expect(cancellation.cancellation_reason).toBe("E2E cancellation reason");
    await expect(sold).toContainText("CANCELLED / UNPAID");

    const expiredView = page.waitForResponse((response) => {
      if (response.request().method() !== "GET") return false;
      const url = new URL(response.url());
      return (
        url.pathname === "/api/operations/memberships/expiry" &&
        url.searchParams.get("bucket") === "today"
      );
    });
    await page
      .getByRole("combobox", { name: "Membership list", exact: true })
      .selectOption({ label: "Expiring today" });
    expect((await expiredView).ok()).toBeTruthy();
    await expect(
      memberships.getByRole("row").filter({ hasText: REVISED_PLAN }),
    ).toHaveCount(0);

    const cancelledView = page.waitForResponse((response) => {
      if (response.request().method() !== "GET") return false;
      const url = new URL(response.url());
      return (
        url.pathname === "/api/operations/memberships" &&
        url.searchParams.get("status") === "CANCELLED"
      );
    });
    await page
      .getByRole("combobox", { name: "Membership list", exact: true })
      .selectOption({ label: "All memberships" });
    await page
      .getByRole("combobox", { name: "Filter by status", exact: true })
      .selectOption("CANCELLED");
    expect((await cancelledView).ok()).toBeTruthy();
    await expect(
      memberships.getByRole("row").filter({ hasText: REVISED_PLAN }),
    ).toContainText("CANCELLED / UNPAID");
  });

  await openSection(page, "Members");
  await memberRow(page, UPDATED_NAME)
    .getByRole("link", { name: UPDATED_NAME, exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Current membership" }),
  ).toContainText("No current membership.");
  await expect(
    page.getByRole("region", { name: "Membership history" }),
  ).toContainText(`${REVISED_PLAN}: ${startDate} – ${startDate} (CANCELLED)`);

  await test.step("archive the plan and the member", async () => {
    await openSection(page, "Memberships");
    await page.getByRole("button", { name: "View plans", exact: true }).click();
    const planRow = plans.getByRole("row").filter({ hasText: PLAN_CODE });
    await planRow
      .getByRole("button", {
        name: `Archive ${REVISED_PLAN} (${PLAN_CODE}) plan`,
        exact: true,
      })
      .click();
    const planArchiveDialog = page.getByRole("dialog", {
      name: `Archive ${REVISED_PLAN}?`,
    });
    await expect(planArchiveDialog).toContainText(
      `${REVISED_PLAN} (${PLAN_CODE})`,
    );
    await planArchiveDialog
      .getByRole("button", { name: "Archive", exact: true })
      .click();
    await expect(planRow).toContainText("Archived");
    const startForm = await openStartMembershipDialog(page);
    const planSelect = startForm.getByRole("combobox", {
      name: "Plan",
      exact: true,
    });
    await expect(
      planSelect.locator("option", { hasText: REVISED_PLAN }),
    ).toHaveCount(0);
    await startForm.getByRole("button", { name: "Close", exact: true }).click();
    await expect(startForm).toHaveCount(0);

    await openSection(page, "Members");
    const directoryRow = memberRow(page, UPDATED_NAME);
    await expect(
      directoryRow.getByRole("button", {
        name: `Archive ${UPDATED_NAME}`,
        exact: true,
      }),
    ).toBeVisible();
    await directoryRow
      .getByRole("link", { name: UPDATED_NAME, exact: true })
      .click();
    await page
      .getByRole("button", { name: "Archive member", exact: true })
      .click();
    const archiveDialog = page.getByRole("dialog", {
      name: `Archive ${UPDATED_NAME} (${memberNumber})?`,
    });
    await expect(archiveDialog).toContainText("not deleted");
    await expect(archiveDialog).toContainText("history remain");
    const archived = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/archive\/?$/.test(new URL(response.url()).pathname),
    );
    await archiveDialog
      .getByRole("button", { name: "Archive", exact: true })
      .click();
    const archiveResponse = await archived;
    expect(archiveResponse.ok()).toBeTruthy();
    expect((await responseData(archiveResponse)).status).toBe("ARCHIVED");
    await expect(page.getByText("ARCHIVED", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Archive member", exact: true }),
    ).toHaveCount(0);

    await openSection(page, "Members");
    await settleMembers(
      page,
      () =>
        page.getByLabel("Search members", { exact: true }).fill(UPDATED_FIRST),
      { search: UPDATED_FIRST, limit: "25" },
    );
    await expect(memberRow(page, UPDATED_NAME)).toContainText("ARCHIVED");
    await settleMembers(
      page,
      () => memberStatusOption(page, "ACTIVE").click(),
      { status: "ACTIVE", search: UPDATED_FIRST, limit: "25" },
    );
    await expect(memberRow(page, UPDATED_NAME)).toHaveCount(0);
    await settleMembers(
      page,
      () => memberStatusOption(page, "ARCHIVED").click(),
      { status: "ARCHIVED", search: UPDATED_FIRST, limit: "25" },
    );
    await expect(memberRow(page, UPDATED_NAME)).toContainText("ARCHIVED");
    await settleMembers(
      page,
      () =>
        page
          .getByLabel("Search members", { exact: true })
          .fill("nomatch-e2e-phase"),
      { status: "ARCHIVED", search: "nomatch-e2e-phase", limit: "25" },
    );
    await expect(
      page.getByText("No members found.", { exact: true }),
    ).toBeVisible();

    await openSection(page, "Memberships");
    const archivedSearch = await openStartMembershipDialog(page);
    await archivedSearch
      .getByLabel("Search members", { exact: true })
      .fill(UPDATED_FIRST);
    await expect(
      archivedSearch.getByText("No matching members.", { exact: true }),
    ).toBeVisible();
    await expect(
      archivedSearch.getByRole("list", { name: "Matching members" }),
    ).toHaveCount(0);
    await archivedSearch
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await expect(archivedSearch).toHaveCount(0);
  });

  await test.step("show a rotating desk code without storing it", async () => {
    const scans: string[] = [];
    const recordScan = (request: { url: () => string }): void => {
      if (request.url().includes("/check-ins/scan")) scans.push(request.url());
    };
    page.on("request", recordScan);
    const issued = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/api\/operations\/check-in-challenges\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await openSection(page, "Check-ins");
    await page.getByRole("tab", { name: "QR display", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Onsite check-in code", exact: true }),
    ).toBeVisible();
    const first = await issued;
    const firstBody = await first.text();
    expect(first.status(), firstBody).toBe(201);
    const firstPayload = challengePayload(firstBody);
    await expectChallengeStaysOnScreen(page, firstPayload);

    const rotated = page.waitForResponse(
      (response) =>
        settledResponse(response) &&
        response.request().method() === "POST" &&
        /\/api\/operations\/check-in-challenges\/?$/.test(
          new URL(response.url()).pathname,
        ),
    );
    await page
      .getByLabel("Display location", { exact: true })
      .fill("Side entrance");
    await page
      .getByRole("button", { name: "Update location", exact: true })
      .click();
    const second = await rotated;
    const secondBody = await second.text();
    expect(second.status(), secondBody).toBe(201);
    const secondPayload = challengePayload(secondBody);
    expect(secondPayload).not.toBe(firstPayload);
    await expectChallengeStaysOnScreen(page, secondPayload);
    await expectChallengeStaysOnScreen(page, firstPayload);
    expect(scans).toEqual([]);
    page.off("request", recordScan);
    await expectNoAlert(page);
  });

  const html = await page.content();
  for (const secret of secrets) {
    expect(html.toLowerCase()).not.toContain(secret.toLowerCase());
  }
  await expectNoBrowserSecrets(page, secrets);
  await assertResponses();
});

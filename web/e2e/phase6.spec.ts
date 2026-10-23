import {
  expect,
  test,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";

import {
  expectNoBrowserSecrets,
  ownerAccount,
  signIn,
  watchJsonResponses,
} from "./auth";

const SOURCES = [
  "NEW_MEMBER",
  "RENEWAL",
  "PT",
  "CLASS",
  "PRODUCT",
  "OTHER",
] as const;

const SOURCE_LABELS: Record<(typeof SOURCES)[number], string> = {
  NEW_MEMBER: "New member",
  RENEWAL: "Member renewal",
  PT: "PT",
  CLASS: "Class",
  PRODUCT: "Product sales",
  OTHER: "Other",
};

const OPERATIONAL_METRICS = [
  ["Active members", "active_members"],
  ["New members", "new_members"],
  ["Expired memberships", "expired_memberships"],
  ["Check-ins", "check_ins"],
  ["Unique attendees", "unique_attendees"],
  ["Currently checked in", "currently_checked_in"],
  ["Expiring in 3 days", "expiring_within_3_days"],
  ["Expiring in 7 days", "expiring_within_7_days"],
  ["Expiring in 30 days", "expiring_within_30_days"],
  ["Pending approvals", "pending_approvals"],
] as const;

type OperationalKey = (typeof OPERATIONAL_METRICS)[number][1];

type DashboardPayload = {
  range: { date_from: string; date_to: string };
  timezone: string;
  currency: string;
  operational: Record<OperationalKey, number>;
  financial: {
    total_revenue: string;
    class_revenue: string;
    pending_payment_member_count: number;
    pending_payment_amount: string;
    revenue_sources_ranked: { source: string; amount: string }[];
    top_product_items: {
      name: string;
      quantity_sold: string;
      revenue: string;
      gross_profit: string;
    }[];
    total_expenses: string;
    expenses_by_category: { system_key: string; amount: string }[];
    staff_salary_expense: string;
    pt_commission_expense: string;
    net_profit: string;
  };
};

test("owner dashboard loads through the BFF and follows the server totals", async ({
  page,
}) => {
  const secrets = [ownerAccount.password, ownerAccount.totpSecret];
  const assertResponses = watchJsonResponses(page, secrets);
  const directDashboardCalls = trackDirectDashboardCalls(page);

  const todayResponse = page.waitForResponse(dashboardResponse("today"));
  await signIn(page, ownerAccount);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Owner dashboard", exact: true }),
  ).toBeVisible();
  const todayHttp = await todayResponse;
  expect(originOf(todayHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const today = await readDashboard(todayHttp);
  await expectRenderedDashboard(page, today);
  await expect(
    page.getByRole("button", { name: "Today", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");

  const monthResponse = page.waitForResponse(dashboardResponse("this_month"));
  await page.getByRole("button", { name: "This month", exact: true }).click();
  const monthHttp = await monthResponse;
  expect(originOf(monthHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const month = await readDashboard(monthHttp);
  expect(rangeText(month)).not.toBe(rangeText(today));
  await expectRenderedDashboard(page, month);
  await expect(
    page.getByRole("button", { name: "This month", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Today", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("p.error[role='alert']")).toHaveCount(0);
  expect(directDashboardCalls).toEqual([]);
  await expectNoBrowserSecrets(page, secrets);
  await assertResponses();
});

test("owner daily and monthly reports load through the BFF", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const secrets = [ownerAccount.password, ownerAccount.totpSecret];
  const assertResponses = watchJsonResponses(page, secrets);
  const directReportCalls = trackDirectReportCalls(page);

  await signIn(page, ownerAccount);
  await page.getByRole("link", { name: "Reports", exact: true }).click();
  await expect(page).toHaveURL(/\/reports$/);
  await expect(
    page.getByRole("heading", { name: "Reports", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText(
    "Choose a date, month, or range.",
  );
  const reportType = page.getByRole("combobox", {
    name: "Report type",
    exact: true,
  });
  const generateReport = page.getByRole("button", {
    name: "Generate report",
    exact: true,
  });

  await page.getByLabel("Date", { exact: true }).fill("2026-10-01");
  const dailyResponse = page.waitForResponse(
    reportResponse("daily", { date: "2026-10-01" }),
  );
  await generateReport.click();
  const dailyHttp = await dailyResponse;
  expect(originOf(dailyHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const daily = await readPeriodReport(dailyHttp);
  expect(daily.rows).toHaveLength(1);
  await expectRenderedReport(page, "Daily report", daily);
  const exportResponse = page.waitForResponse((response) => {
    if (!response.ok() || response.request().method() !== "GET") return false;
    const url = new URL(response.url());
    return (
      url.pathname.replace(/\/$/, "") ===
        "/api/operations/reports/daily/export" &&
      url.searchParams.get("date") === "2026-10-01" &&
      !url.searchParams.has("after")
    );
  });
  await page.getByRole("button", { name: "Export CSV", exact: true }).click();
  const exported = await exportResponse;
  expect(exported.headers()["content-type"] ?? "").toContain("text/csv");
  expect(exported.headers()["content-disposition"] ?? "").toContain(
    "attachment;",
  );
  await expect(page.locator("p.error[role='alert']")).toHaveCount(0);

  await reportType.selectOption("monthly");
  await page.getByLabel("Reporting month").fill("2026-10");
  const monthlyResponse = page.waitForResponse(
    reportResponse("monthly", { year: "2026", month: "10" }),
  );
  await generateReport.click();
  const monthlyHttp = await monthlyResponse;
  expect(originOf(monthlyHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const monthly = await readPeriodReport(monthlyHttp);
  expect(monthly.rows.length).toBeGreaterThan(1);
  expect(monthly.rows.length).toBeLessThanOrEqual(31);
  expect(monthly.range.date_from).not.toBe(monthly.range.date_to);
  await expectRenderedReport(page, "Monthly report", monthly);

  await reportType.selectOption("cash-book");
  await page.getByLabel("From date").fill("2026-10-01");
  await page.getByLabel("To date").fill("2026-10-03");
  const cashResponse = page.waitForResponse(
    reportResponse("cashBook", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const cashHttp = await cashResponse;
  expect(originOf(cashHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const cash = await readCashBook(cashHttp);
  expect(cash.rows).toHaveLength(3);
  await expectRenderedCashBook(page, cash);

  await reportType.selectOption("profit-loss");
  const profitResponse = page.waitForResponse(
    reportResponse("profitLoss", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const profitHttp = await profitResponse;
  expect(originOf(profitHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const profit = await readProfitLoss(profitHttp);
  expect(profit.rows).toHaveLength(3);
  await expectRenderedProfitLoss(page, profit);

  await reportType.selectOption("membership-expiry");
  const expiryResponse = page.waitForResponse(
    reportResponse("membershipExpiry", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const expiryHttp = await expiryResponse;
  expect(originOf(expiryHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const expiry = await readOperationalReport(expiryHttp, "membership expiry");
  expect(expiry.rows).toHaveLength(3);
  await expectRenderedOperational(page, "Membership expiry", expiry, [
    [
      "Expiring memberships",
      requiredCount(expiry.totals, "expiring_memberships"),
    ],
    ["Unique members", requiredCount(expiry.totals, "unique_members")],
  ]);

  await reportType.selectOption("check-ins");
  const checkInResponse = page.waitForResponse(
    reportResponse("checkIns", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const checkInHttp = await checkInResponse;
  expect(originOf(checkInHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const checkIns = await readOperationalReport(checkInHttp, "check-ins");
  expect(checkIns.rows).toHaveLength(3);
  await expectRenderedOperational(page, "Check-ins", checkIns, [
    ["Check-ins", requiredCount(checkIns.totals, "check_ins")],
    [
      "Currently checked in",
      requiredCount(checkIns.totals, "currently_checked_in"),
    ],
  ]);

  await reportType.selectOption("pt-sessions");
  const sessionResponse = page.waitForResponse(
    reportResponse("ptSessions", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const sessionHttp = await sessionResponse;
  expect(originOf(sessionHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const sessions = await readOperationalReport(sessionHttp, "PT sessions");
  expect(sessions.rows).toHaveLength(3);
  await expectRenderedOperational(page, "PT sessions", sessions, [
    ["Bookings", requiredCount(sessions.totals, "bookings")],
    ["Unique members", requiredCount(sessions.totals, "unique_members")],
    ["Unique trainers", requiredCount(sessions.totals, "unique_trainers")],
  ]);

  await reportType.selectOption("classes");
  const classResponse = page.waitForResponse(
    reportResponse("classes", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const classHttp = await classResponse;
  expect(originOf(classHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const classes = await readOperationalReport(classHttp, "classes");
  expect(classes.rows).toHaveLength(3);
  await expectRenderedOperational(page, "Classes", classes, [
    ["Occurrences", requiredCount(classes.totals, "occurrences")],
    ["Remaining capacity", requiredCount(classes.totals, "remaining_capacity")],
    ["Unique trainers", requiredCount(classes.totals, "unique_trainers")],
  ]);

  await reportType.selectOption("product-sales");
  const productResponse = page.waitForResponse(
    reportResponse("productSales", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
      limit: "25",
    }),
  );
  await generateReport.click();
  const productHttp = await productResponse;
  expect(originOf(productHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const productSales = await readProductSales(productHttp);
  await expectRenderedProductSales(page, productSales);

  await reportType.selectOption("stock-movements");
  const stockResponse = page.waitForResponse(
    reportResponse("stockMovements", {
      date_from: "2026-10-01",
      date_to: "2026-10-03",
    }),
  );
  await generateReport.click();
  const stockHttp = await stockResponse;
  expect(originOf(stockHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const stock = await readStockMovements(stockHttp);
  expect(stock.rows).toHaveLength(3);
  await expectRenderedStock(page, stock);

  await reportType.selectOption("inventory");
  const inventoryResponse = page.waitForResponse(
    reportResponse("inventory", { limit: "25" }),
  );
  await generateReport.click();
  const inventoryHttp = await inventoryResponse;
  expect(originOf(inventoryHttp.url(), page.url())).toBe(
    originOf(page.url(), page.url()),
  );
  const inventory = await readInventory(inventoryHttp);
  await expectRenderedInventory(page, inventory);
  await expect(page.locator("p.error[role='alert']")).toHaveCount(0);
  expect(directReportCalls).toEqual([]);
  await expectNoBrowserSecrets(page, secrets);
  await assertResponses();
});

const BFF_DASHBOARD_PATH = "/api/operations/dashboard";

function dashboardResponse(range: string) {
  return (response: Response): boolean => {
    if (!response.ok() || response.request().method() !== "GET") return false;
    const requestUrl = response.request().url();
    return [response.url(), requestUrl].some((value) =>
      isBffDashboardUrl(value, requestUrl, range),
    );
  };
}

function isBffDashboardUrl(
  value: string,
  base: string,
  range: string,
): boolean {
  const url = parseSameOriginUrl(value, base);
  if (!url) return false;
  const pathname = url.pathname.endsWith("/")
    ? url.pathname.slice(0, -1)
    : url.pathname;
  return (
    pathname === BFF_DASHBOARD_PATH &&
    url.searchParams.get("range") === range &&
    url.searchParams.size === 1
  );
}

function parseSameOriginUrl(value: string, base: string): URL | null {
  try {
    return new URL(value);
  } catch {
    try {
      return new URL(value, base);
    } catch {
      return null;
    }
  }
}

function originOf(value: string, base: string): string {
  const url = parseSameOriginUrl(value, base);
  if (!url)
    throw new Error("Dashboard response URL was not on this page's origin.");
  return url.origin;
}

function trackDirectDashboardCalls(page: Page): string[] {
  const calls: string[] = [];
  page.on("request", (request) => {
    let url: URL;
    try {
      url = new URL(request.url());
    } catch {
      return;
    }
    if (url.pathname.replace(/\/$/, "") === "/api/v1/dashboard") {
      calls.push(`${request.method()} ${url.pathname}`);
    }
  });
  return calls;
}

async function readDashboard(response: Response): Promise<DashboardPayload> {
  const contentType = response.headers()["content-type"] ?? "";
  if (!contentType.includes("json")) {
    throw new Error("Dashboard response was not JSON from the BFF.");
  }
  return parseDashboard(await response.json());
}

function parseDashboard(body: unknown): DashboardPayload {
  const envelope = record(body, "dashboard envelope");
  if (envelope.success !== true) {
    throw new Error("Dashboard response was not a success envelope.");
  }
  const data = record(envelope.data, "dashboard data");
  if (data.financial === null) {
    throw new Error("The owner dashboard omitted financial totals.");
  }
  const range = record(data.range, "dashboard range");
  const financial = record(data.financial, "dashboard financial");
  const operational = record(data.operational, "dashboard operational");
  const counts = {} as Record<OperationalKey, number>;
  for (const [, key] of OPERATIONAL_METRICS) {
    counts[key] = nonNegativeInteger(operational[key], key);
  }
  const ranked = arrayOf(financial.revenue_sources_ranked, "revenue source");
  const sources = ranked.map((item) => {
    const row = record(item, "revenue source");
    const source = text(row.source, "revenue source");
    return { source, amount: money(row.amount, source) };
  });
  const seen = new Set(sources.map((item) => item.source));
  const hasEverySource = SOURCES.every((source) => seen.has(source));
  if (sources.length !== SOURCES.length || !hasEverySource) {
    throw new Error("Dashboard revenue sources were not the six server rows.");
  }
  const classRow = sources.find((item) => item.source === "CLASS");
  const classRevenue = money(financial.class_revenue, "class_revenue");
  if (!classRow || classRow.amount !== classRevenue) {
    throw new Error("Class revenue did not match its ranked server row.");
  }
  const products = arrayOf(financial.top_product_items, "product").map(
    (item) => {
      const row = record(item, "product");
      return {
        name: text(row.name, "product name"),
        quantity_sold: text(row.quantity_sold, "quantity_sold"),
        revenue: money(row.revenue, "product revenue"),
        gross_profit: money(row.gross_profit, "product gross profit"),
      };
    },
  );
  if (products.length > 10) {
    throw new Error("Dashboard returned more than 10 product rows.");
  }
  const expenses = arrayOf(financial.expenses_by_category, "expense").map(
    (item) => {
      const row = record(item, "expense");
      return {
        system_key: text(row.system_key, "expense category"),
        amount: money(row.amount, "expense amount"),
      };
    },
  );
  if (expenses.length !== 10) {
    throw new Error("Dashboard did not return the ten protected expense rows.");
  }
  return {
    range: {
      date_from: isoDate(range.date_from, "date_from"),
      date_to: isoDate(range.date_to, "date_to"),
    },
    timezone: text(data.timezone, "timezone"),
    currency: text(data.currency, "currency"),
    operational: counts,
    financial: {
      total_revenue: money(financial.total_revenue, "total_revenue"),
      class_revenue: classRevenue,
      pending_payment_member_count: nonNegativeInteger(
        financial.pending_payment_member_count,
        "pending_payment_member_count",
      ),
      pending_payment_amount: money(
        financial.pending_payment_amount,
        "pending_payment_amount",
      ),
      revenue_sources_ranked: sources,
      top_product_items: products,
      total_expenses: money(financial.total_expenses, "total_expenses"),
      expenses_by_category: expenses,
      staff_salary_expense: money(
        financial.staff_salary_expense,
        "staff_salary_expense",
      ),
      pt_commission_expense: money(
        financial.pt_commission_expense,
        "pt_commission_expense",
      ),
      net_profit: money(financial.net_profit, "net_profit"),
    },
  };
}

async function expectRenderedDashboard(
  page: Page,
  data: DashboardPayload,
): Promise<void> {
  await expect(
    page.getByText("Loading dashboard…", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Financial totals are not included in this response.", {
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByText(`${data.range.date_from} to ${data.range.date_to}`, {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(data.timezone, { exact: true })).toBeVisible();

  const operational = page.getByRole("region", { name: "Operational totals" });
  await expect(
    operational.getByRole("heading", { name: "Operational totals" }),
  ).toBeVisible();
  for (const [label, key] of OPERATIONAL_METRICS) {
    await expect(
      metric(operational, label).getByText(String(data.operational[key]), {
        exact: true,
      }),
    ).toBeVisible();
  }

  const financial = page.getByRole("region", { name: "Financial totals" });
  await expect(
    financial.getByRole("heading", { name: "Financial totals" }),
  ).toBeVisible();
  await expectMoney(
    financial,
    "Total revenue",
    data.financial.total_revenue,
    data,
  );
  await expectMoney(
    financial,
    "Total expenses",
    data.financial.total_expenses,
    data,
  );
  await expectMoney(financial, "Net profit", data.financial.net_profit, data);
  await expectMoney(
    financial,
    "Staff salary",
    data.financial.staff_salary_expense,
    data,
  );
  await expectMoney(
    financial,
    "PT commission",
    data.financial.pt_commission_expense,
    data,
  );
  const pending = metric(financial, "Pending payments");
  await expect(
    pending.getByText(
      moneyText(data.financial.pending_payment_amount, data.currency),
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    pending.getByText(
      `${data.financial.pending_payment_member_count} members`,
      {
        exact: true,
      },
    ),
  ).toBeVisible();

  const revenue = card(financial, "Revenue sources");
  const revenueRows = revenue.getByRole("listitem");
  await expect(revenueRows).toHaveCount(
    data.financial.revenue_sources_ranked.length,
  );
  for (const [
    index,
    source,
  ] of data.financial.revenue_sources_ranked.entries()) {
    const label = SOURCE_LABELS[source.source as (typeof SOURCES)[number]];
    if (!label) throw new Error(`Unexpected revenue source ${source.source}.`);
    const row = revenueRows.nth(index);
    await expect(row).toContainText(label);
    await expect(
      row.getByText(moneyText(source.amount, data.currency), { exact: true }),
    ).toBeVisible();
    await expect(
      row.getByText(String(index + 1).padStart(2, "0"), { exact: true }),
    ).toBeVisible();
  }

  const products = card(financial, "Top products");
  if (data.financial.top_product_items.length === 0) {
    await expect(
      products.getByText("No product sales in this period.", { exact: true }),
    ).toBeVisible();
  } else {
    const productRows = products.getByRole("listitem");
    await expect(productRows).toHaveCount(
      data.financial.top_product_items.length,
    );
    for (const [index, product] of data.financial.top_product_items.entries()) {
      const row = productRows.nth(index);
      await expect(row).toContainText(product.name);
      await expect(row).toContainText(product.quantity_sold);
      await expect(row).toContainText(
        moneyText(product.revenue, data.currency),
      );
      await expect(row).toContainText(
        moneyText(product.gross_profit, data.currency),
      );
    }
  }

  const expenses = card(financial, "Expense overview");
  const expenseRows = expenses.getByRole("listitem");
  await expect(expenseRows).toHaveCount(
    data.financial.expenses_by_category.length,
  );
  for (const [
    index,
    expense,
  ] of data.financial.expenses_by_category.entries()) {
    const row = expenseRows.nth(index);
    await expect(
      row.getByText(expense.system_key, { exact: true }),
    ).toBeVisible();
    await expect(
      row.getByText(moneyText(expense.amount, data.currency), { exact: true }),
    ).toBeVisible();
  }
}

function card(region: Locator, name: string): Locator {
  return region.locator("section").filter({
    has: region.page().getByRole("heading", { name, exact: true }),
  });
}

function metric(region: Locator, label: string): Locator {
  return region.getByText(label, { exact: true }).locator("..");
}

async function expectMoney(
  region: Locator,
  label: string,
  amount: string,
  data: DashboardPayload,
): Promise<void> {
  await expect(
    metric(region, label).getByText(moneyText(amount, data.currency), {
      exact: true,
    }),
  ).toBeVisible();
}

function moneyText(amount: string, currency: string): string {
  return `${amount} ${currency}`;
}

function rangeText(data: DashboardPayload): string {
  return `${data.range.date_from} to ${data.range.date_to}. ${data.timezone}. ${data.currency}.`;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Expected ${label} to be an object.`);
  }
  return value as Record<string, unknown>;
}

function arrayOf(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Expected ${label} rows.`);
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`Expected ${label} text.`);
  }
  return value;
}

function money(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^-?\d+\.\d{2}$/.test(value)) {
    throw new Error(`Expected ${label} to be server-formatted money.`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error(`Expected ${label} to be a non-negative integer.`);
  }
  return value;
}

function isoDate(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`Expected ${label} to be a date.`);
  }
  return value;
}

const BFF_REPORT_PATHS = {
  daily: "/api/operations/reports/daily",
  monthly: "/api/operations/reports/monthly",
  cashBook: "/api/operations/reports/cash-book",
  profitLoss: "/api/operations/reports/profit-loss",
  membershipExpiry: "/api/operations/reports/membership-expiry",
  checkIns: "/api/operations/reports/check-ins",
  ptSessions: "/api/operations/reports/pt-sessions",
  classes: "/api/operations/reports/classes",
  productSales: "/api/operations/reports/product-sales",
  stockMovements: "/api/operations/reports/stock-movements",
  inventory: "/api/operations/reports/inventory",
} as const;

type PeriodReportPayload = {
  gym: { code: string; name: string };
  timezone: string;
  currency: string;
  range: { date_from: string; date_to: string };
  generated_at: string;
  source_status_policy: {
    revenue: string;
    expenses: string;
    operations: string;
  };
  rows: {
    date: string;
    total_revenue: string;
    total_expenses: string;
    net_profit: string;
    product_quantity: string;
  }[];
  totals: {
    unique_attendees: number;
    total_revenue: string;
    total_expenses: string;
    net_profit: string;
    staff_salary_expense: string;
    pt_commission_expense: string;
    expenses_by_category: { system_key: string; amount: string }[];
  };
};

function reportResponse(
  kind: keyof typeof BFF_REPORT_PATHS,
  query: Record<string, string>,
) {
  return (response: Response): boolean => {
    if (!response.ok() || response.request().method() !== "GET") return false;
    const requestUrl = response.request().url();
    return [response.url(), requestUrl].some((value) =>
      isBffReportUrl(value, requestUrl, kind, query),
    );
  };
}

function isBffReportUrl(
  value: string,
  base: string,
  kind: keyof typeof BFF_REPORT_PATHS,
  query: Record<string, string>,
): boolean {
  const url = parseSameOriginUrl(value, base);
  if (!url) return false;
  const pathname = url.pathname.endsWith("/")
    ? url.pathname.slice(0, -1)
    : url.pathname;
  if (pathname !== BFF_REPORT_PATHS[kind]) return false;
  if (url.searchParams.size !== Object.keys(query).length) return false;
  return Object.entries(query).every(
    ([key, expected]) => url.searchParams.get(key) === expected,
  );
}

function trackDirectReportCalls(page: Page): string[] {
  const calls: string[] = [];
  page.on("request", (request) => {
    let url: URL;
    try {
      url = new URL(request.url());
    } catch {
      return;
    }
    const pathname = url.pathname.replace(/\/$/, "");
    if (
      pathname === "/api/v1/reports/daily" ||
      pathname === "/api/v1/reports/daily/export" ||
      pathname === "/api/v1/reports/monthly" ||
      pathname === "/api/v1/reports/monthly/export" ||
      pathname === "/api/v1/reports/cash-book" ||
      pathname === "/api/v1/reports/cash-book/export" ||
      pathname === "/api/v1/reports/profit-loss" ||
      pathname === "/api/v1/reports/profit-loss/export" ||
      pathname === "/api/v1/reports/membership-expiry" ||
      pathname === "/api/v1/reports/membership-expiry/export" ||
      pathname === "/api/v1/reports/check-ins" ||
      pathname === "/api/v1/reports/check-ins/export" ||
      pathname === "/api/v1/reports/pt-sessions" ||
      pathname === "/api/v1/reports/pt-sessions/export" ||
      pathname === "/api/v1/reports/classes" ||
      pathname === "/api/v1/reports/classes/export" ||
      pathname === "/api/v1/reports/product-sales" ||
      pathname === "/api/v1/reports/product-sales/export" ||
      pathname === "/api/v1/reports/stock-movements" ||
      pathname === "/api/v1/reports/stock-movements/export" ||
      pathname === "/api/v1/reports/inventory" ||
      pathname === "/api/v1/reports/inventory/export"
    ) {
      calls.push(`${request.method()} ${url.pathname}`);
    }
  });
  return calls;
}

async function readPeriodReport(
  response: Response,
): Promise<PeriodReportPayload> {
  const body = record(await response.json(), "period report envelope");
  const data = record(body.data, "period report");
  const gym = record(data.gym, "gym");
  const range = record(data.range, "range");
  const policy = record(data.source_status_policy, "source status");
  const totals = record(data.totals, "totals");
  const rows = arrayOf(data.rows, "report").map((row, index) => {
    const entry = record(row, `row ${index}`);
    return {
      date: isoDate(entry.date, "row date"),
      total_revenue: money(entry.total_revenue, "row revenue"),
      total_expenses: money(entry.total_expenses, "row expenses"),
      net_profit: money(entry.net_profit, "row profit"),
      product_quantity: quantity(entry.product_quantity, "row quantity"),
    };
  });
  return {
    gym: { code: text(gym.code, "gym code"), name: text(gym.name, "gym name") },
    timezone: text(data.timezone, "timezone"),
    currency: text(data.currency, "currency"),
    range: {
      date_from: isoDate(range.date_from, "date from"),
      date_to: isoDate(range.date_to, "date to"),
    },
    generated_at: text(data.generated_at, "generated at"),
    source_status_policy: {
      revenue: text(policy.revenue, "revenue policy"),
      expenses: text(policy.expenses, "expense policy"),
      operations: text(policy.operations, "operations policy"),
    },
    rows,
    totals: {
      unique_attendees: nonNegativeInteger(
        totals.unique_attendees,
        "unique attendees",
      ),
      total_revenue: money(totals.total_revenue, "total revenue"),
      total_expenses: money(totals.total_expenses, "total expenses"),
      net_profit: money(totals.net_profit, "net profit"),
      staff_salary_expense: money(totals.staff_salary_expense, "staff salary"),
      pt_commission_expense: money(
        totals.pt_commission_expense,
        "PT commission",
      ),
      expenses_by_category: arrayOf(
        totals.expenses_by_category,
        "categories",
      ).map((category, index) => {
        const entry = record(category, `category ${index}`);
        return {
          system_key: text(entry.system_key, "category"),
          amount: money(entry.amount, "category amount"),
        };
      }),
    },
  };
}

function quantity(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^-?\d+\.\d{3}$/.test(value)) {
    throw new Error(`Expected ${label} to be a three-decimal quantity.`);
  }
  return value;
}

async function expectRenderedReport(
  page: Page,
  title: string,
  report: PeriodReportPayload,
): Promise<void> {
  const reportSection = page.getByRole("region", { name: title, exact: true });
  await expect(
    reportSection.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await expect(
    reportSection.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expect(
    reportSection.getByText(report.gym.code, { exact: true }),
  ).toBeVisible();
  await expect(
    reportSection.getByText(
      `${report.range.date_from} to ${report.range.date_to}`,
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    reportSection.getByText(report.timezone, { exact: true }),
  ).toBeVisible();
  await expect(
    reportSection.getByText(report.currency, { exact: true }),
  ).toBeVisible();
  const generatedLabel = new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(report.generated_at));
  await expect(
    reportSection.getByText(`Generated ${generatedLabel}`, { exact: true }),
  ).toBeVisible();
  await revealPolicy(reportSection);
  await expect(
    reportSection.getByText(report.source_status_policy.revenue, {
      exact: true,
    }),
  ).toBeVisible();
  const first = report.rows[0];
  const last = report.rows[report.rows.length - 1];
  if (!first || !last) throw new Error("Expected at least one report row.");
  const firstRow = reportSection.getByRole("row").filter({
    has: page.getByRole("rowheader", { name: first.date, exact: true }),
  });
  await expect(firstRow).toContainText(first.total_revenue);
  await expect(firstRow).toContainText(first.net_profit);
  await expect(firstRow).toContainText(first.product_quantity);
  await expect(
    reportSection.getByRole("rowheader", { name: last.date, exact: true }),
  ).toBeVisible();
  const totals = reportSection.getByRole("region", {
    name: "Totals",
    exact: true,
  });
  await expectTotal(
    totals,
    "Unique attendees",
    String(report.totals.unique_attendees),
  );
  await expectTotal(totals, "Net profit", report.totals.net_profit);
  await expectTotal(totals, "Staff salary", report.totals.staff_salary_expense);
  await expectTotal(
    totals,
    "PT commission",
    report.totals.pt_commission_expense,
  );
  const category = report.totals.expenses_by_category[0];
  if (!category) throw new Error("Expected expense categories.");
  await expect(totals).toContainText(
    `${category.system_key} ${category.amount}`,
  );
}

function requiredCount(totals: Record<string, number>, key: string): number {
  const value = totals[key];
  if (value === undefined) {
    throw new Error(`Expected ${key} in report totals.`);
  }
  return value;
}

function expectTotal(totals: Locator, label: string, value: string) {
  const item = totals.locator("div").filter({
    has: totals.page().getByText(label, { exact: true }),
  });
  return expect(item.locator("dd")).toHaveText(value);
}

type OperationalPayload = {
  gym: { code: string; name: string };
  timezone: string;
  range: { date_from: string; date_to: string };
  generated_at: string;
  source_status_policy: string;
  rows: { date: string }[];
  totals: Record<string, number>;
};

async function readOperationalReport(
  response: Response,
  label: string,
): Promise<OperationalPayload> {
  const body = record(await response.json(), `${label} envelope`);
  const data = record(body.data, label);
  if ("currency" in data) {
    throw new Error(`Expected ${label} to omit currency.`);
  }
  const gym = record(data.gym, `${label} gym`);
  const range = record(data.range, `${label} range`);
  if (!Array.isArray(data.rows)) {
    throw new Error(`Expected ${label} rows.`);
  }
  const totals = record(data.totals, `${label} totals`);
  const parsedTotals: Record<string, number> = {};
  for (const [key, value] of Object.entries(totals)) {
    parsedTotals[key] = nonNegativeInteger(value, `${label} ${key}`);
  }
  return {
    gym: {
      code: text(gym.code, `${label} code`),
      name: text(gym.name, `${label} name`),
    },
    timezone: text(data.timezone, `${label} timezone`),
    range: {
      date_from: isoDate(range.date_from, `${label} from`),
      date_to: isoDate(range.date_to, `${label} to`),
    },
    generated_at: text(data.generated_at, `${label} generated`),
    source_status_policy: text(data.source_status_policy, `${label} policy`),
    rows: data.rows.map((row, index) => {
      const entry = record(row, `${label} row ${index}`);
      return { date: isoDate(entry.date, `${label} row date`) };
    }),
    totals: parsedTotals,
  };
}

type ProductSalesPayload = {
  gym: { code: string; name: string };
  timezone: string;
  currency: string;
  source_status_policy: string;
  totals: { revenue: string; product_count: number };
  meta: { has_more: boolean };
  firstSku: string | null;
};

type StockPayload = {
  gym: { code: string; name: string };
  timezone: string;
  source_status_policy: string;
  rows: { date: string; net_quantity: string }[];
  totals: { net_quantity: string };
};

type InventoryPayload = {
  gym: { code: string; name: string };
  timezone: string;
  source_status_policy: string;
  totals: { product_count: number; total_stock_quantity: string };
  meta: { has_more: boolean };
  firstSku: string | null;
};

async function readProductSales(
  response: Response,
): Promise<ProductSalesPayload> {
  const body = record(await response.json(), "product sales envelope");
  const data = record(body.data, "product sales");
  const gym = record(data.gym, "product sales gym");
  const totals = record(data.totals, "product sales totals");
  const meta = record(data.meta, "product sales meta");
  const rows = arrayOf(data.rows, "product sales rows");
  const first = rows[0] ? record(rows[0], "product sales row") : null;
  if (typeof meta.has_more !== "boolean") {
    throw new Error("Expected product sales paging.");
  }
  return {
    gym: {
      code: text(gym.code, "product sales code"),
      name: text(gym.name, "product sales name"),
    },
    timezone: text(data.timezone, "product sales timezone"),
    currency: text(data.currency, "product sales currency"),
    source_status_policy: text(
      data.source_status_policy,
      "product sales policy",
    ),
    totals: {
      revenue: money(totals.revenue, "product sales revenue"),
      product_count: nonNegativeInteger(
        totals.product_count,
        "product sales count",
      ),
    },
    meta: { has_more: meta.has_more },
    firstSku: first ? text(first.sku, "product sales sku") : null,
  };
}

async function readStockMovements(response: Response): Promise<StockPayload> {
  const body = record(await response.json(), "stock movement envelope");
  const data = record(body.data, "stock movements");
  if ("currency" in data || "meta" in data) {
    throw new Error("Expected stock movements to omit currency and paging.");
  }
  const gym = record(data.gym, "stock gym");
  const totals = record(data.totals, "stock totals");
  return {
    gym: {
      code: text(gym.code, "stock code"),
      name: text(gym.name, "stock name"),
    },
    timezone: text(data.timezone, "stock timezone"),
    source_status_policy: text(data.source_status_policy, "stock policy"),
    rows: arrayOf(data.rows, "stock rows").map((row, index) => {
      const entry = record(row, `stock row ${index}`);
      return {
        date: isoDate(entry.date, "stock date"),
        net_quantity: quantity(entry.net_quantity, "stock net"),
      };
    }),
    totals: { net_quantity: quantity(totals.net_quantity, "stock total net") },
  };
}

async function readInventory(response: Response): Promise<InventoryPayload> {
  const body = record(await response.json(), "inventory envelope");
  const data = record(body.data, "inventory");
  if ("currency" in data || "range" in data) {
    throw new Error("Expected inventory to omit currency and a date range.");
  }
  const gym = record(data.gym, "inventory gym");
  const totals = record(data.totals, "inventory totals");
  const meta = record(data.meta, "inventory meta");
  const rows = arrayOf(data.rows, "inventory rows");
  const first = rows[0] ? record(rows[0], "inventory row") : null;
  if (typeof meta.has_more !== "boolean") {
    throw new Error("Expected inventory paging.");
  }
  return {
    gym: {
      code: text(gym.code, "inventory code"),
      name: text(gym.name, "inventory name"),
    },
    timezone: text(data.timezone, "inventory timezone"),
    source_status_policy: text(data.source_status_policy, "inventory policy"),
    totals: {
      product_count: nonNegativeInteger(
        totals.product_count,
        "inventory product count",
      ),
      total_stock_quantity: quantity(
        totals.total_stock_quantity,
        "inventory stock",
      ),
    },
    meta: { has_more: meta.has_more },
    firstSku: first ? text(first.sku, "inventory sku") : null,
  };
}

async function expectRenderedProductSales(
  page: Page,
  report: ProductSalesPayload,
) {
  const section = page.getByRole("region", {
    name: "Product sales",
    exact: true,
  });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expect(
    section.getByText(report.currency, { exact: true }),
  ).toBeVisible();
  await expectPolicy(section, report.source_status_policy);
  await expectTotal(
    section.getByRole("region", { name: "Totals", exact: true }),
    "Revenue",
    report.totals.revenue,
  );
  await expectFirstPagePagination(section, report.meta.has_more);
  if (report.firstSku) {
    await expect(
      section.getByRole("rowheader", { name: report.firstSku, exact: true }),
    ).toBeVisible();
  }
}

async function expectRenderedStock(page: Page, report: StockPayload) {
  const section = page.getByRole("region", {
    name: "Stock movements",
    exact: true,
  });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expectPolicy(section, report.source_status_policy);
  await expect(section.getByText("THB", { exact: true })).toHaveCount(0);
  const first = report.rows[0];
  if (!first) throw new Error("Expected a stock movement row.");
  await expect(
    section.getByRole("rowheader", { name: first.date, exact: true }),
  ).toBeVisible();
  await expectTotal(
    section.getByRole("region", { name: "Totals", exact: true }),
    "Net",
    report.totals.net_quantity,
  );
}

async function expectRenderedInventory(page: Page, report: InventoryPayload) {
  const section = page.getByRole("region", { name: "Inventory", exact: true });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expectPolicy(section, report.source_status_policy);
  await expect(section.getByText("THB", { exact: true })).toHaveCount(0);
  await expectTotal(
    section.getByRole("region", { name: "Totals", exact: true }),
    "Products",
    String(report.totals.product_count),
  );
  await expectFirstPagePagination(section, report.meta.has_more);
  if (report.firstSku) {
    await expect(
      section.getByRole("rowheader", { name: report.firstSku, exact: true }),
    ).toBeVisible();
  }
}

async function expectPolicy(section: Locator, policy: string) {
  await revealPolicy(section);
  await expect(section.getByText(policy, { exact: true })).toBeVisible();
}

async function expectFirstPagePagination(section: Locator, hasMore: boolean) {
  const pagination = section.getByRole("navigation", {
    name: "Pagination",
    exact: true,
  });
  if (hasMore) {
    await expect(
      pagination.getByRole("button", { name: "Next page", exact: true }),
    ).toBeVisible();
    await expect(pagination.getByText("Page 1", { exact: true })).toBeVisible();
    return;
  }
  await expect(pagination).toHaveCount(0);
}

async function revealPolicy(section: Locator) {
  const details = section.locator("details").first();
  await expect(details).toHaveCount(1);
  if ((await details.getAttribute("open")) === null) {
    await details.locator("summary").click();
  }
}

async function expectRenderedOperational(
  page: Page,
  title: string,
  report: OperationalPayload,
  totals: readonly (readonly [string, number])[],
) {
  const section = page.getByRole("region", { name: title, exact: true });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expect(
    section.getByText(report.timezone, { exact: true }),
  ).toBeVisible();
  await expectPolicy(section, report.source_status_policy);
  await expect(section.getByText("THB", { exact: true })).toHaveCount(0);
  const first = report.rows[0];
  if (!first) throw new Error(`Expected a ${title} row.`);
  await expect(
    section.getByRole("rowheader", { name: first.date, exact: true }),
  ).toBeVisible();
  const totalsRegion = section.getByRole("region", {
    name: "Totals",
    exact: true,
  });
  for (const [label, value] of totals) {
    await expectTotal(totalsRegion, label, String(value));
  }
}

type CashBookPayload = {
  gym: { code: string; name: string };
  timezone: string;
  currency: string;
  range: { date_from: string; date_to: string };
  generated_at: string;
  source_status_policy: { sessions: string; transactions: string };
  rows: { date: string; net_cash_flow: string }[];
  totals: { net_cash_flow: string; session_count: number };
};

type ProfitLossPayload = {
  gym: { code: string; name: string };
  timezone: string;
  currency: string;
  range: { date_from: string; date_to: string };
  generated_at: string;
  source_status_policy: { revenue: string };
  rows: { date: string; net_profit: string; total_revenue: string }[];
  totals: {
    net_profit: string;
    expenses_by_category: { system_key: string; amount: string }[];
  };
};

async function readCashBook(response: Response): Promise<CashBookPayload> {
  const body = record(await response.json(), "cash book envelope");
  const data = record(body.data, "cash book");
  return {
    ...readIdentity(data, "cash book"),
    source_status_policy: {
      sessions: text(
        record(data.source_status_policy, "cash policy").sessions,
        "session policy",
      ),
      transactions: text(
        record(data.source_status_policy, "cash policy").transactions,
        "transaction policy",
      ),
    },
    rows: arrayOf(data.rows, "cash rows").map((row, index) => {
      const entry = record(row, `cash row ${index}`);
      return {
        date: isoDate(entry.date, "cash date"),
        net_cash_flow: money(entry.net_cash_flow, "net cash flow"),
      };
    }),
    totals: {
      net_cash_flow: money(
        record(data.totals, "cash totals").net_cash_flow,
        "total net cash flow",
      ),
      session_count: nonNegativeInteger(
        record(data.totals, "cash totals").session_count,
        "session count",
      ),
    },
  };
}

async function readProfitLoss(response: Response): Promise<ProfitLossPayload> {
  const body = record(await response.json(), "profit envelope");
  const data = record(body.data, "profit");
  const totals = record(data.totals, "profit totals");
  return {
    ...readIdentity(data, "profit"),
    source_status_policy: {
      revenue: text(
        record(data.source_status_policy, "profit policy").revenue,
        "revenue policy",
      ),
    },
    rows: arrayOf(data.rows, "profit rows").map((row, index) => {
      const entry = record(row, `profit row ${index}`);
      return {
        date: isoDate(entry.date, "profit date"),
        total_revenue: money(entry.total_revenue, "profit revenue"),
        net_profit: money(entry.net_profit, "profit net"),
      };
    }),
    totals: {
      net_profit: money(totals.net_profit, "profit total"),
      expenses_by_category: arrayOf(
        totals.expenses_by_category,
        "profit categories",
      ).map((category, index) => {
        const entry = record(category, `profit category ${index}`);
        return {
          system_key: text(entry.system_key, "profit category"),
          amount: money(entry.amount, "profit category amount"),
        };
      }),
    },
  };
}

function readIdentity(data: Record<string, unknown>, label: string) {
  const gym = record(data.gym, `${label} gym`);
  const range = record(data.range, `${label} range`);
  return {
    gym: {
      code: text(gym.code, `${label} code`),
      name: text(gym.name, `${label} name`),
    },
    timezone: text(data.timezone, `${label} timezone`),
    currency: text(data.currency, `${label} currency`),
    range: {
      date_from: isoDate(range.date_from, `${label} from`),
      date_to: isoDate(range.date_to, `${label} to`),
    },
    generated_at: text(data.generated_at, `${label} generated`),
  };
}

async function expectRenderedCashBook(page: Page, report: CashBookPayload) {
  const section = page.getByRole("region", { name: "Cash book", exact: true });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await revealPolicy(section);
  await expect(
    section.getByText(report.source_status_policy.transactions, {
      exact: true,
    }),
  ).toBeVisible();
  const first = report.rows[0];
  if (!first) throw new Error("Expected a cash-book row.");
  await expect(
    section.getByRole("rowheader", { name: first.date, exact: true }),
  ).toBeVisible();
  const totals = section.getByRole("region", { name: "Totals", exact: true });
  await expectTotal(totals, "Net cash flow", report.totals.net_cash_flow);
  await expectTotal(totals, "Sessions", String(report.totals.session_count));
}

async function expectRenderedProfitLoss(page: Page, report: ProfitLossPayload) {
  const section = page.getByRole("region", {
    name: "Profit and loss",
    exact: true,
  });
  await expect(
    section.getByText(report.gym.name, { exact: true }),
  ).toBeVisible();
  await expect(
    section.getByText(`${report.range.date_from} to ${report.range.date_to}`, {
      exact: true,
    }),
  ).toBeVisible();
  await revealPolicy(section);
  await expect(
    section.getByText(report.source_status_policy.revenue, { exact: true }),
  ).toBeVisible();
  const totals = section.getByRole("region", { name: "Totals", exact: true });
  await expectTotal(totals, "Net profit", report.totals.net_profit);
  const category = report.totals.expenses_by_category[0];
  if (!category) throw new Error("Expected profit expense categories.");
  await expect(totals).toContainText(
    `${category.system_key} ${category.amount}`,
  );
}

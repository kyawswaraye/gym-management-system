const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const YEAR = /^(?:19|20)\d{2}$/;
const MONTH = /^(?:0?[1-9]|1[0-2])$/;
const MAX_INCLUSIVE_DAYS = 366;

const DAILY_PATH = "/api/v1/reports/daily/";
const MONTHLY_PATH = "/api/v1/reports/monthly/";
const CASH_BOOK_PATH = "/api/v1/reports/cash-book/";
const PROFIT_LOSS_PATH = "/api/v1/reports/profit-loss/";
const MEMBERSHIP_EXPIRY_PATH = "/api/v1/reports/membership-expiry/";
const CHECK_INS_PATH = "/api/v1/reports/check-ins/";
const PT_SESSIONS_PATH = "/api/v1/reports/pt-sessions/";
const CLASSES_PATH = "/api/v1/reports/classes/";
const PRODUCT_SALES_PATH = "/api/v1/reports/product-sales/";
const STOCK_MOVEMENTS_PATH = "/api/v1/reports/stock-movements/";
const INVENTORY_PATH = "/api/v1/reports/inventory/";
const TRAINER_DASHBOARD_PATH = "/api/v1/trainer/dashboard/";
const TRAINER_SESSION_REPORT_PATH = "/api/v1/trainer/reports/sessions/";
const DASHBOARD_PRESETS = new Set([
  "today",
  "this_week",
  "this_month",
  "last_month",
  "this_year",
]);
const RANGE_PATHS = new Set([
  CASH_BOOK_PATH,
  PROFIT_LOSS_PATH,
  MEMBERSHIP_EXPIRY_PATH,
  CHECK_INS_PATH,
  PT_SESSIONS_PATH,
  CLASSES_PATH,
  STOCK_MOVEMENTS_PATH,
]);

const EXPORT_PATH =
  /^\/api\/v1\/reports\/(daily|monthly|cash-book|profit-loss|product-sales|membership-expiry|check-ins|pt-sessions|classes|stock-movements|inventory)\/export\/$/;

export const REPORT_PATH_PATTERNS: readonly RegExp[] = [
  /^\/api\/v1\/reports\/daily\/$/,
  /^\/api\/v1\/reports\/monthly\/$/,
  /^\/api\/v1\/reports\/cash-book\/$/,
  /^\/api\/v1\/reports\/profit-loss\/$/,
  /^\/api\/v1\/reports\/membership-expiry\/$/,
  /^\/api\/v1\/reports\/check-ins\/$/,
  /^\/api\/v1\/reports\/pt-sessions\/$/,
  /^\/api\/v1\/reports\/classes\/$/,
  /^\/api\/v1\/reports\/product-sales\/$/,
  /^\/api\/v1\/reports\/stock-movements\/$/,
  /^\/api\/v1\/reports\/inventory\/$/,
  EXPORT_PATH,
  /^\/api\/v1\/trainer\/dashboard\/$/,
  /^\/api\/v1\/trainer\/reports\/sessions\/$/,
];

function strictQuery(
  search: string,
  allowed: ReadonlySet<string>,
): Map<string, string> | null {
  if (search.length === 0) return new Map();
  const values = new Map<string, string>();
  for (const [key, value] of new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  ).entries()) {
    if (!allowed.has(key) || values.has(key) || value.length === 0) {
      return null;
    }
    values.set(key, value);
  }
  return values;
}

function realDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (!match?.[1] || !match[2] || !match[3]) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1900 || year > 2099) return false;
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

function inclusiveDays(dateFrom: string, dateTo: string): number | null {
  const start = dateParts(dateFrom);
  const end = dateParts(dateTo);
  if (!start || !end) return null;
  const days =
    (Date.UTC(end.year, end.month - 1, end.day) -
      Date.UTC(start.year, start.month - 1, start.day)) /
      86_400_000 +
    1;
  return days;
}

function dateParts(
  value: string,
): { year: number; month: number; day: number } | null {
  const match = ISO_DATE.exec(value);
  if (!match?.[1] || !match[2] || !match[3] || !realDate(value)) return null;
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
  };
}

function allowsDaily(search: string): boolean {
  const values = strictQuery(search, new Set(["date"]));
  if (!values || values.size !== 1) return false;
  const date = values.get("date");
  return date !== undefined && realDate(date);
}

function allowsMonthly(search: string): boolean {
  const values = strictQuery(search, new Set(["year", "month"]));
  if (!values || values.size !== 2) return false;
  const year = values.get("year");
  const month = values.get("month");
  return (
    year !== undefined &&
    month !== undefined &&
    YEAR.test(year) &&
    MONTH.test(month)
  );
}

function allowsLimit(value: string | undefined): boolean {
  return value !== undefined && /^(?:[1-9]|[1-9]\d|100)$/.test(value);
}

function allowsCursor(value: string | undefined): boolean {
  return value !== undefined && value.length > 0 && value.length <= 2048;
}

function allowsProductSales(search: string): boolean {
  const values = strictQuery(
    search,
    new Set(["date_from", "date_to", "limit", "after"]),
  );
  if (!values || !values.has("date_from") || !values.has("date_to")) {
    return false;
  }
  const dateFrom = values.get("date_from");
  const dateTo = values.get("date_to");
  if (dateFrom === undefined || dateTo === undefined) return false;
  const days = inclusiveDays(dateFrom, dateTo);
  if (days === null || days < 1 || days > MAX_INCLUSIVE_DAYS) return false;
  if (values.has("limit") && !allowsLimit(values.get("limit"))) return false;
  if (values.has("after") && !allowsCursor(values.get("after"))) return false;
  return true;
}

function allowsInventory(search: string): boolean {
  const values = strictQuery(search, new Set(["limit", "after"]));
  if (!values) return false;
  if (values.has("limit") && !allowsLimit(values.get("limit"))) return false;
  if (values.has("after") && !allowsCursor(values.get("after"))) return false;
  return true;
}

function allowsTrainerDashboard(search: string): boolean {
  const values = strictQuery(
    search,
    new Set(["range", "date_from", "date_to"]),
  );
  if (!values) return false;
  const range = values.get("range");
  if (range !== undefined) {
    return values.size === 1 && DASHBOARD_PRESETS.has(range);
  }
  if (!values.has("date_from") || !values.has("date_to") || values.size !== 2) {
    return false;
  }
  return allowsRange(search);
}

function allowsRange(search: string): boolean {
  const values = strictQuery(search, new Set(["date_from", "date_to"]));
  if (!values || values.size !== 2) return false;
  const dateFrom = values.get("date_from");
  const dateTo = values.get("date_to");
  if (dateFrom === undefined || dateTo === undefined) return false;
  const days = inclusiveDays(dateFrom, dateTo);
  return days !== null && days >= 1 && days <= MAX_INCLUSIVE_DAYS;
}

export function allowsReportExport(
  pathname: string,
  search: string,
  method: string | undefined,
): boolean {
  const match = EXPORT_PATH.exec(pathname);
  const kind = match?.[1];
  if (!kind || method !== "GET") return false;
  if (kind === "daily") return allowsDaily(search);
  if (kind === "monthly") return allowsMonthly(search);
  if (kind === "inventory") return search.length === 0;
  return allowsRange(search);
}

export function allowsReportRoute(
  pathname: string,
  search: string,
  method: string | undefined,
): boolean | null {
  if (EXPORT_PATH.test(pathname)) {
    return allowsReportExport(pathname, search, method);
  }
  if (pathname === TRAINER_DASHBOARD_PATH) {
    return method === "GET" && allowsTrainerDashboard(search);
  }
  if (pathname === TRAINER_SESSION_REPORT_PATH) {
    return method === "GET" && allowsRange(search);
  }
  if (
    pathname !== DAILY_PATH &&
    pathname !== MONTHLY_PATH &&
    pathname !== PRODUCT_SALES_PATH &&
    pathname !== INVENTORY_PATH &&
    !RANGE_PATHS.has(pathname)
  ) {
    return null;
  }
  if (method !== "GET") return false;
  if (pathname === DAILY_PATH) return allowsDaily(search);
  if (pathname === MONTHLY_PATH) return allowsMonthly(search);
  if (pathname === PRODUCT_SALES_PATH) return allowsProductSales(search);
  if (pathname === INVENTORY_PATH) return allowsInventory(search);
  return allowsRange(search);
}

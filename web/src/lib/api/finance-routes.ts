const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CANONICAL_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LIMIT = /^(?:[1-9]|[1-9]\d|100)$/;
const CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const REGISTER = /^[A-Z0-9_-]{1,32}$/;
const SKU = /^[A-Z0-9][A-Z0-9_-]{0,31}$/;

const WORKFLOW_STATUSES = new Set([
  "DRAFT",
  "PENDING_APPROVAL",
  "POSTED",
  "REJECTED",
  "CANCELLED",
  "VOID",
]);
const METHODS = new Set([
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
]);
const PURPOSES = new Set(["MEMBERSHIP", "PT", "PRODUCT", "CLASS", "OTHER"]);
const CASH_STATUSES = new Set(["OPEN", "CLOSED", "VOID"]);
const DIRECTIONS = new Set(["IN", "OUT"]);
const SOURCE_TYPES = new Set([
  "PAYMENT",
  "EXPENSE",
  "COMMISSION",
  "MANUAL",
  "REVERSAL",
]);
const RECEIPT_STATUSES = new Set(["ISSUED", "VOID", "REPLACED"]);
const MOVEMENT_TYPES = new Set([
  "OPENING",
  "PURCHASE",
  "SALE",
  "SALE_RETURN",
  "DAMAGE",
  "LOSS",
  "ADJUSTMENT",
  "CORRECTION",
]);

const LIST = new Set(["GET"]);
const READ = new Set(["GET"]);
const CREATE = new Set(["POST"]);
const UPDATE = new Set(["PATCH"]);
const REPLACE = new Set(["PUT"]);
const COMMAND = new Set(["POST"]);
const COLLECTION = new Set(["GET", "POST"]);

type Rule = {
  pattern: RegExp;
  methods: ReadonlySet<string>;
  filters?: ReadonlySet<string>;
  check?: (values: ReadonlyMap<string, string>) => boolean;
};

function id(path: string): RegExp {
  return new RegExp(`^/api/v1/${path}/${UUID}/$`);
}

function action(path: string, name: string): RegExp {
  return new RegExp(`^/api/v1/${path}/${UUID}/${name}/$`);
}

function enumValue(
  values: ReadonlyMap<string, string>,
  key: string,
  allowed: ReadonlySet<string>,
): boolean {
  const value = values.get(key);
  return value === undefined || allowed.has(value);
}

function uuidValue(values: ReadonlyMap<string, string>, key: string): boolean {
  const value = values.get(key);
  return value === undefined || CANONICAL_UUID.test(value);
}

function dateValue(values: ReadonlyMap<string, string>, key: string): boolean {
  const value = values.get(key);
  if (value === undefined) return true;
  const match = ISO_DATE.exec(value);
  if (!match?.[1] || !match[2] || !match[3]) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

function flagValue(values: ReadonlyMap<string, string>, key: string): boolean {
  const value = values.get(key);
  return value === undefined || value === "true" || value === "false";
}

function textValue(
  values: ReadonlyMap<string, string>,
  key: string,
  pattern: RegExp,
): boolean {
  const value = values.get(key);
  return value === undefined || pattern.test(value);
}

const FINANCE_ROUTES: readonly Rule[] = [
  {
    pattern: /^\/api\/v1\/cash-sessions\/$/,
    methods: LIST,
    filters: new Set([
      "status",
      "register_code",
      "business_date",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", CASH_STATUSES) &&
      textValue(values, "register_code", REGISTER) &&
      dateValue(values, "business_date"),
  },
  {
    pattern: /^\/api\/v1\/cash-sessions\/open\/$/,
    methods: CREATE,
  },
  {
    pattern: id("cash-sessions"),
    methods: READ,
  },
  {
    pattern: action("cash-sessions", "adjustments"),
    methods: COMMAND,
  },
  {
    pattern: action("cash-sessions", "reconcile"),
    methods: COMMAND,
  },
  {
    pattern: /^\/api\/v1\/cash-transactions\/$/,
    methods: LIST,
    filters: new Set([
      "cash_session_id",
      "business_date",
      "source_type",
      "direction",
      "limit",
      "after",
    ]),
    check: (values) =>
      uuidValue(values, "cash_session_id") &&
      dateValue(values, "business_date") &&
      enumValue(values, "source_type", SOURCE_TYPES) &&
      enumValue(values, "direction", DIRECTIONS),
  },
  {
    pattern: /^\/api\/v1\/payments\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "business_date",
      "member",
      "membership",
      "method",
      "purpose",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", WORKFLOW_STATUSES) &&
      dateValue(values, "business_date") &&
      uuidValue(values, "member") &&
      uuidValue(values, "membership") &&
      enumValue(values, "method", METHODS) &&
      enumValue(values, "purpose", PURPOSES),
  },
  {
    pattern: id("payments"),
    methods: new Set(["GET", "PATCH"]),
  },
  {
    pattern: action("payments", "submit"),
    methods: COMMAND,
  },
  {
    pattern: action("payments", "approve"),
    methods: COMMAND,
  },
  {
    pattern: action("payments", "reject"),
    methods: COMMAND,
  },
  {
    pattern: action("payments", "cancel"),
    methods: COMMAND,
  },
  {
    pattern: action("payments", "void"),
    methods: COMMAND,
  },
  {
    pattern: action("payments", "refund"),
    methods: COMMAND,
  },
  {
    pattern: /^\/api\/v1\/receipts\/$/,
    methods: LIST,
    filters: new Set([
      "status",
      "business_date",
      "member",
      "payment",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", RECEIPT_STATUSES) &&
      dateValue(values, "business_date") &&
      uuidValue(values, "member") &&
      uuidValue(values, "payment"),
  },
  {
    pattern: id("receipts"),
    methods: READ,
  },
  {
    pattern: action("receipts", "download"),
    methods: READ,
  },
  {
    pattern: action("receipts", "void"),
    methods: COMMAND,
  },
  {
    pattern: /^\/api\/v1\/expense-categories\/$/,
    methods: COLLECTION,
    filters: new Set(["is_active", "limit", "after"]),
    check: (values) => flagValue(values, "is_active"),
  },
  {
    pattern: id("expense-categories"),
    methods: UPDATE,
  },
  {
    pattern: /^\/api\/v1\/expenses\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "business_date",
      "category",
      "payment_method",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", WORKFLOW_STATUSES) &&
      dateValue(values, "business_date") &&
      uuidValue(values, "category") &&
      enumValue(values, "payment_method", METHODS),
  },
  {
    pattern: id("expenses"),
    methods: new Set(["GET", "PATCH"]),
  },
  {
    pattern: action("expenses", "submit"),
    methods: COMMAND,
  },
  {
    pattern: action("expenses", "approve"),
    methods: COMMAND,
  },
  {
    pattern: action("expenses", "reject"),
    methods: COMMAND,
  },
  {
    pattern: action("expenses", "cancel"),
    methods: COMMAND,
  },
  {
    pattern: action("expenses", "void"),
    methods: COMMAND,
  },
  {
    pattern: /^\/api\/v1\/salary-periods\/$/,
    methods: COLLECTION,
    filters: new Set(["status", "business_date", "limit", "after"]),
    check: (values) =>
      enumValue(values, "status", WORKFLOW_STATUSES) &&
      dateValue(values, "business_date"),
  },
  {
    pattern: id("salary-periods"),
    methods: new Set(["GET", "PATCH"]),
  },
  {
    pattern: action("salary-periods", "lines"),
    methods: REPLACE,
  },
  {
    pattern: action("salary-periods", "submit"),
    methods: COMMAND,
  },
  {
    pattern: action("salary-periods", "approve"),
    methods: COMMAND,
  },
  {
    pattern: action("salary-periods", "reject"),
    methods: COMMAND,
  },
  {
    pattern: action("salary-periods", "cancel"),
    methods: COMMAND,
  },
  {
    pattern: action("salary-periods", "void"),
    methods: COMMAND,
  },
  {
    pattern: /^\/api\/v1\/product-categories\/$/,
    methods: COLLECTION,
    filters: new Set(["is_active", "limit", "after"]),
    check: (values) => flagValue(values, "is_active"),
  },
  {
    pattern: /^\/api\/v1\/products\/$/,
    methods: COLLECTION,
    filters: new Set([
      "category_id",
      "is_active",
      "sku",
      "barcode",
      "limit",
      "after",
    ]),
    check: (values) =>
      uuidValue(values, "category_id") &&
      flagValue(values, "is_active") &&
      textValue(values, "sku", SKU) &&
      textValue(values, "barcode", /^[A-Za-z0-9._:-]{1,80}$/),
  },
  {
    pattern: /^\/api\/v1\/products\/low-stock\/$/,
    methods: LIST,
    filters: new Set(["category_id", "limit", "after"]),
    check: (values) => uuidValue(values, "category_id"),
  },
  {
    pattern: id("products"),
    methods: new Set(["GET", "PATCH"]),
  },
  {
    pattern: /^\/api\/v1\/stock-movements\/$/,
    methods: LIST,
    filters: new Set([
      "product_id",
      "movement_type",
      "business_date",
      "limit",
      "after",
    ]),
    check: (values) =>
      uuidValue(values, "product_id") &&
      enumValue(values, "movement_type", MOVEMENT_TYPES) &&
      dateValue(values, "business_date"),
  },
  {
    pattern: /^\/api\/v1\/stock-movements\/adjust\/$/,
    methods: CREATE,
  },
  {
    pattern: /^\/api\/v1\/retail-sales\/$/,
    methods: COLLECTION,
    filters: new Set(["status", "business_date", "member", "limit", "after"]),
    check: (values) =>
      enumValue(values, "status", WORKFLOW_STATUSES) &&
      dateValue(values, "business_date") &&
      uuidValue(values, "member"),
  },
  {
    pattern: id("retail-sales"),
    methods: new Set(["GET", "PATCH"]),
  },
  {
    pattern: action("retail-sales", "submit"),
    methods: COMMAND,
  },
  {
    pattern: action("retail-sales", "approve"),
    methods: COMMAND,
  },
  {
    pattern: action("retail-sales", "reject"),
    methods: COMMAND,
  },
  {
    pattern: action("retail-sales", "cancel"),
    methods: COMMAND,
  },
  {
    pattern: action("retail-sales", "void"),
    methods: COMMAND,
  },
  {
    pattern: action("retail-sales", "return"),
    methods: COMMAND,
  },
];

export const FINANCE_PATH_PATTERNS: readonly RegExp[] = FINANCE_ROUTES.map(
  (rule) => rule.pattern,
);

function hasQueryMeta(value: string): boolean {
  return /[\u0000-\u001f\u007f&#?]/.test(value);
}

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

function allowsFilters(search: string, rule: Rule): boolean {
  if (!rule.filters) return search.length === 0;
  const values = strictQuery(search, rule.filters);
  if (!values) return false;
  for (const value of values.values()) {
    if (hasQueryMeta(value)) return false;
  }
  const limit = values.get("limit");
  if (limit !== undefined && !LIMIT.test(limit)) return false;
  const after = values.get("after");
  if (after !== undefined && !CURSOR.test(after)) return false;
  if (
    rule.filters.has("business_date") &&
    !dateValue(values, "business_date")
  ) {
    return false;
  }
  return rule.check ? rule.check(values) : true;
}

export function allowsFinanceRoute(
  pathname: string,
  search: string,
  method: string | undefined,
): boolean | null {
  const rule = FINANCE_ROUTES.find((candidate) =>
    candidate.pattern.test(pathname),
  );
  if (!rule) return null;
  if (!method || !rule.methods.has(method)) return false;
  if (method !== "GET") return search.length === 0;
  return allowsFilters(search, rule);
}

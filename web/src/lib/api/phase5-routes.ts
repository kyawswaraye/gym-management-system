const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CANONICAL_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LIMIT = /^(?:[1-9]|[1-9]\d|100)$/;
const CURSOR = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

const LIST = new Set(["GET"]);
const CREATE = new Set(["POST"]);
const UPDATE = new Set(["PATCH"]);
const COMMAND = new Set(["POST"]);
const COLLECTION = new Set(["GET", "POST"]);
const READ_UPDATE = new Set(["GET", "PATCH"]);

const ASSIGNMENT_STATUS = new Set(["ACTIVE", "ENDED", "CANCELLED"]);
const SALE_STATUS = new Set([
  "DRAFT",
  "PENDING_APPROVAL",
  "POSTED",
  "REJECTED",
  "CANCELLED",
  "VOID",
]);
const COMMISSION_STATUS = new Set([
  "ACCRUED",
  "PARTIALLY_PAID",
  "PAID",
  "VOID",
]);
const PACKAGE_TYPE = new Set(["PT", "CLASS"]);
const GRANT_STATUS = new Set(["ACTIVE", "EXHAUSTED", "EXPIRED", "VOID"]);
const BOOKING_STATUS = new Set(["BOOKED", "ATTENDED", "NO_SHOW", "CANCELLED"]);
const SCHEDULE_STATUS = new Set(["ACTIVE", "ENDED", "CANCELLED"]);
const OCCURRENCE_STATUS = new Set(["SCHEDULED", "CANCELLED", "COMPLETED"]);
const LIST_VIEW = new Set(["list", "calendar"]);

type IdempotencyPolicy = "required" | "rejected" | "none";

type Rule = {
  pattern: RegExp;
  methods: ReadonlySet<string>;
  filters?: ReadonlySet<string>;
  check?: (values: ReadonlyMap<string, string>) => boolean;
  idempotency: IdempotencyPolicy;
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

function orderedDates(
  values: ReadonlyMap<string, string>,
  startKey: string,
  endKey: string,
): boolean {
  const start = values.get(startKey);
  const end = values.get(endKey);
  if (!start || !end) return true;
  return end >= start;
}

function calendarSpan(values: ReadonlyMap<string, string>): boolean {
  if (values.get("view") !== "calendar") return true;
  const start = values.get("date_from");
  const end = values.get("date_to");
  if (
    !start ||
    !end ||
    !dateValue(values, "date_from") ||
    !dateValue(values, "date_to")
  ) {
    return false;
  }
  const startMatch = ISO_DATE.exec(start);
  const endMatch = ISO_DATE.exec(end);
  if (!startMatch || !endMatch) return false;
  const startUtc = Date.UTC(
    Number(startMatch[1]),
    Number(startMatch[2]) - 1,
    Number(startMatch[3]),
  );
  const endUtc = Date.UTC(
    Number(endMatch[1]),
    Number(endMatch[2]) - 1,
    Number(endMatch[3]),
  );
  const days = (endUtc - startUtc) / 86_400_000 + 1;
  return days >= 1 && days <= 42;
}

const PHASE5_ROUTES: readonly Rule[] = [
  {
    pattern: /^\/api\/v1\/trainers\/$/,
    methods: LIST,
    filters: new Set(["limit", "after"]),
    idempotency: "none",
  },
  {
    pattern: action("trainers", "assigned-members"),
    methods: LIST,
    filters: new Set(["limit", "after"]),
    idempotency: "none",
  },
  {
    pattern: /^\/api\/v1\/trainer-assignments\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "trainer_staff_profile_id",
      "member_id",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", ASSIGNMENT_STATUS) &&
      uuidValue(values, "trainer_staff_profile_id") &&
      uuidValue(values, "member_id"),
    idempotency: "none",
  },
  {
    pattern: id("trainer-assignments"),
    methods: LIST,
    idempotency: "none",
  },
  {
    pattern: action("trainer-assignments", "end"),
    methods: COMMAND,
    idempotency: "none",
  },
  {
    pattern: action("trainer-assignments", "cancel"),
    methods: COMMAND,
    idempotency: "none",
  },
  {
    pattern: /^\/api\/v1\/service-packages\/$/,
    methods: COLLECTION,
    filters: new Set(["type", "is_active", "limit", "after"]),
    check: (values) =>
      enumValue(values, "type", PACKAGE_TYPE) && flagValue(values, "is_active"),
    idempotency: "none",
  },
  {
    pattern: /^\/api\/v1\/member-service-packages\/$/,
    methods: COLLECTION,
    filters: new Set(["member_id", "status", "package_type", "limit", "after"]),
    check: (values) =>
      uuidValue(values, "member_id") &&
      enumValue(values, "status", GRANT_STATUS) &&
      enumValue(values, "package_type", PACKAGE_TYPE),
    idempotency: "required",
  },
  {
    pattern: id("member-service-packages"),
    methods: LIST,
    idempotency: "none",
  },
  {
    pattern: action("member-service-packages", "ledger"),
    methods: LIST,
    filters: new Set(["limit", "after"]),
    idempotency: "none",
  },
  {
    pattern: new RegExp(
      `^/api/v1/member-service-packages/${UUID}/ledger/adjust/$`,
    ),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/pt-sales\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "member_id",
      "trainer_staff_profile_id",
      "service_date_from",
      "service_date_to",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", SALE_STATUS) &&
      uuidValue(values, "member_id") &&
      uuidValue(values, "trainer_staff_profile_id") &&
      dateValue(values, "service_date_from") &&
      dateValue(values, "service_date_to") &&
      orderedDates(values, "service_date_from", "service_date_to"),
    idempotency: "required",
  },
  {
    pattern: id("pt-sales"),
    methods: READ_UPDATE,
    idempotency: "required",
  },
  {
    pattern: action("pt-sales", "submit"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-sales", "approve"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-sales", "reject"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-sales", "cancel"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-sales", "void"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/pt-commissions\/$/,
    methods: LIST,
    filters: new Set([
      "status",
      "trainer_staff_profile_id",
      "service_date_from",
      "service_date_to",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", COMMISSION_STATUS) &&
      uuidValue(values, "trainer_staff_profile_id") &&
      dateValue(values, "service_date_from") &&
      dateValue(values, "service_date_to") &&
      orderedDates(values, "service_date_from", "service_date_to"),
    idempotency: "none",
  },
  {
    pattern: id("pt-commissions"),
    methods: LIST,
    idempotency: "none",
  },
  {
    pattern: action("pt-commissions", "record-payment"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/pt-bookings\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "trainer_staff_profile_id",
      "member_id",
      "date_from",
      "date_to",
      "view",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", BOOKING_STATUS) &&
      uuidValue(values, "trainer_staff_profile_id") &&
      uuidValue(values, "member_id") &&
      dateValue(values, "date_from") &&
      dateValue(values, "date_to") &&
      orderedDates(values, "date_from", "date_to") &&
      enumValue(values, "view", LIST_VIEW) &&
      calendarSpan(values),
    idempotency: "required",
  },
  {
    pattern: id("pt-bookings"),
    methods: LIST,
    idempotency: "none",
  },
  {
    pattern: action("pt-bookings", "attend"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-bookings", "no-show"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("pt-bookings", "cancel"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/class-types\/$/,
    methods: COLLECTION,
    filters: new Set(["is_active", "limit", "after"]),
    check: (values) => flagValue(values, "is_active"),
    idempotency: "none",
  },
  {
    pattern: id("class-types"),
    methods: UPDATE,
    idempotency: "none",
  },
  {
    pattern: /^\/api\/v1\/class-schedules\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "trainer_staff_profile_id",
      "class_type_id",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", SCHEDULE_STATUS) &&
      uuidValue(values, "trainer_staff_profile_id") &&
      uuidValue(values, "class_type_id"),
    idempotency: "required",
  },
  {
    pattern: id("class-schedules"),
    methods: UPDATE,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/class-occurrences\/$/,
    methods: LIST,
    filters: new Set([
      "status",
      "trainer_staff_profile_id",
      "date_from",
      "date_to",
      "view",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", OCCURRENCE_STATUS) &&
      uuidValue(values, "trainer_staff_profile_id") &&
      dateValue(values, "date_from") &&
      dateValue(values, "date_to") &&
      orderedDates(values, "date_from", "date_to") &&
      (values.get("view") === undefined || values.get("view") === "calendar") &&
      calendarSpan(values),
    idempotency: "none",
  },
  {
    pattern: action("class-occurrences", "cancel"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/class-bookings\/$/,
    methods: COLLECTION,
    filters: new Set([
      "status",
      "member_id",
      "trainer_staff_profile_id",
      "date_from",
      "date_to",
      "view",
      "limit",
      "after",
    ]),
    check: (values) =>
      enumValue(values, "status", BOOKING_STATUS) &&
      uuidValue(values, "member_id") &&
      uuidValue(values, "trainer_staff_profile_id") &&
      dateValue(values, "date_from") &&
      dateValue(values, "date_to") &&
      orderedDates(values, "date_from", "date_to") &&
      enumValue(values, "view", LIST_VIEW) &&
      calendarSpan(values),
    idempotency: "required",
  },
  {
    pattern: action("class-bookings", "attend"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("class-bookings", "no-show"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: action("class-bookings", "cancel"),
    methods: COMMAND,
    idempotency: "required",
  },
  {
    pattern: /^\/api\/v1\/trainer\/check-ins\/scan\/$/,
    methods: CREATE,
    idempotency: "rejected",
  },
];

export const PHASE5_PATH_PATTERNS: readonly RegExp[] = PHASE5_ROUTES.map(
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
  return rule.check ? rule.check(values) : true;
}

function matchingRule(pathname: string): Rule | undefined {
  return PHASE5_ROUTES.find((candidate) => candidate.pattern.test(pathname));
}

export function allowsPhase5Route(
  pathname: string,
  search: string,
  method: string | undefined,
): boolean | null {
  const rule = matchingRule(pathname);
  if (!rule) return null;
  if (!method || !rule.methods.has(method)) return false;
  if (method !== "GET") return search.length === 0;
  return allowsFilters(search, rule);
}

export function phase5Idempotency(
  pathname: string,
  method: string,
): IdempotencyPolicy | null {
  const rule = matchingRule(pathname);
  if (!rule || !rule.methods.has(method)) return null;
  if (method === "GET" || method === "DELETE") return "none";
  return rule.idempotency;
}

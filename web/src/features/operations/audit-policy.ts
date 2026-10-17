const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LIMIT_PATTERN = /^(?:[1-9]|[1-9]\d|100)$/;
const CURSOR_PATTERN = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const OUTCOMES = new Set(["SUCCESS", "DENIED", "ERROR"]);

export type TenantAuditFilters = {
  action: string;
  module: string;
  outcome: string;
  targetType: string;
  targetId: string;
  limit: string;
  after?: string;
};

export type TenantAuditPathResult =
  { ok: true; path: string } | { ok: false; message: string };

export function tenantAuditScopeLabel(scope: "GYM" | "SELF"): string {
  return scope === "GYM" ? "All gym events" : "Your actions only";
}

export function buildTenantAuditListPath(
  filters: TenantAuditFilters,
): TenantAuditPathResult {
  const params = new URLSearchParams();
  const action = boundedText(
    filters.action,
    100,
    "Enter an action up to 100 characters.",
  );
  if (!action.ok) return action;
  if (action.value) params.set("action", action.value);

  const moduleName = boundedText(
    filters.module,
    50,
    "Enter a module up to 50 characters.",
  );
  if (!moduleName.ok) return moduleName;
  if (moduleName.value) params.set("module", moduleName.value);

  const outcome = filters.outcome.trim();
  if (outcome) {
    if (!OUTCOMES.has(outcome)) {
      return { ok: false, message: "Choose a valid outcome." };
    }
    params.set("outcome", outcome);
  }

  const targetType = boundedText(
    filters.targetType,
    100,
    "Enter a target type up to 100 characters.",
  );
  if (!targetType.ok) return targetType;
  if (targetType.value) params.set("target_type", targetType.value);

  const targetId = filters.targetId.trim().toLowerCase();
  if (targetId) {
    if (!UUID_PATTERN.test(targetId)) {
      return { ok: false, message: "Enter a valid target ID." };
    }
    params.set("target_id", targetId);
  }

  const limit = filters.limit.trim();
  if (!LIMIT_PATTERN.test(limit)) {
    return { ok: false, message: "Enter a page size from 1 to 100." };
  }
  params.set("limit", limit);

  if (filters.after) {
    if (!CURSOR_PATTERN.test(filters.after)) {
      return { ok: false, message: "The audit cursor is invalid." };
    }
    params.set("after", filters.after);
  }

  return { ok: true, path: `audit-logs?${params.toString()}` };
}

export function isSensitiveAuditKey(key: string): boolean {
  const normalized = key.toLowerCase();
  if (
    normalized.includes("password") ||
    normalized.includes("token") ||
    normalized.includes("secret") ||
    normalized.includes("credential")
  ) {
    return true;
  }
  const compact = normalized.replace(/[^a-z0-9]/g, "");
  return (
    compact.includes("useragent") ||
    compact.includes("ipaddress") ||
    compact === "ip" ||
    compact.includes("gymid") ||
    compact === "email" ||
    compact.startsWith("email") ||
    compact.endsWith("email") ||
    compact === "user" ||
    compact === "userid" ||
    compact.endsWith("userid") ||
    compact.endsWith("actoruser")
  );
}

export function redactTenantAuditValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactTenantAuditValue);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => [
        key,
        isSensitiveAuditKey(key) ? "[REDACTED]" : redactTenantAuditValue(child),
      ]),
    );
  }
  return value;
}

export function formatTenantAuditValue(value: unknown): string {
  try {
    return JSON.stringify(redactTenantAuditValue(value), null, 2);
  } catch {
    return "Unavailable";
  }
}

function boundedText(
  value: string,
  maxLength: number,
  message: string,
): { ok: true; value: string } | { ok: false; message: string } {
  const trimmed = value.trim();
  if (!trimmed) return { ok: true, value: "" };
  if (trimmed.length > maxLength) return { ok: false, message };
  return { ok: true, value: trimmed };
}

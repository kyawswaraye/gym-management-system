const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LIMIT_PATTERN = /^(?:[1-9]|[1-9]\d|100)$/;
const CURSOR_PATTERN = /^[A-Za-z0-9._:~+=-]{1,2048}$/;
const SENSITIVE_KEY = /password|token|secret|credential/i;
const OUTCOMES = new Set(["SUCCESS", "DENIED", "ERROR"]);

export type AuditListFilters = {
  targetGymId: string;
  action: string;
  outcome: string;
  limit: string;
  after?: string;
};

export type AuditListPathResult =
  { ok: true; path: string } | { ok: false; message: string };

export function buildAuditListPath(
  filters: AuditListFilters,
): AuditListPathResult {
  const params = new URLSearchParams();
  const targetGymId = filters.targetGymId.trim().toLowerCase();
  if (targetGymId) {
    if (!UUID_PATTERN.test(targetGymId)) {
      return { ok: false, message: "Enter a valid target gym ID." };
    }
    params.set("target_gym_id", targetGymId);
  }

  const action = filters.action.trim();
  if (action) {
    if (action.length > 100) {
      return { ok: false, message: "Enter an action up to 100 characters." };
    }
    params.set("action", action);
  }

  const outcome = filters.outcome.trim();
  if (outcome) {
    if (!OUTCOMES.has(outcome)) {
      return { ok: false, message: "Choose a valid outcome." };
    }
    params.set("outcome", outcome);
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

  return { ok: true, path: `admin/audit-logs?${params.toString()}` };
}

export function redactAuditValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactAuditValue);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => [
        key,
        SENSITIVE_KEY.test(key) ? "[REDACTED]" : redactAuditValue(child),
      ]),
    );
  }
  return value;
}

export function formatAuditValue(value: unknown): string {
  try {
    return JSON.stringify(redactAuditValue(value), null, 2);
  } catch {
    return "Unavailable";
  }
}

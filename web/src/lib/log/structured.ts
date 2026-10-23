import "server-only";

const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const REDACTED = "[redacted]";
const MAX_DEPTH = 8;
const MAX_ITEMS = 40;
const MAX_STRING = 512;
const SENSITIVE_PARTS = new Set([
  "authorization",
  "cookie",
  "cookies",
  "setcookie",
  "password",
  "passwd",
  "secret",
  "token",
  "ticket",
  "tickets",
  "mfa",
  "otp",
  "totp",
  "challenge",
  "reset",
  "invitation",
  "invite",
  "pepper",
  "credential",
  "credentials",
  "bff",
  "bearer",
  "jwt",
  "signature",
  "presigned",
  "session",
  "apikey",
  "privatekey",
  "accesskey",
  "secretkey",
  "email",
  "mail",
  "query",
  "headers",
  "header",
]);
const BODY_PARTS = new Set([
  "body",
  "rawbody",
  "requestbody",
  "responsebody",
  "payload",
]);
const SUFFIXES = [
  "token",
  "secret",
  "password",
  "cookie",
  "ticket",
  "signature",
  "pepper",
  "apikey",
  "privatekey",
  "credential",
];
const SECRET_TEXT =
  /bearer\s+[A-Za-z0-9._~+/=-]{8,}|x-amz-(?:signature|credential|security-token)=[^&\s]+|(?:access_token|refresh_token|id_token|token|ticket|password|secret|signature|reset_token|invitation(?:_token)?|mfa(?:_challenge_token)?|challenge_token|bff_shared_secret|x-gym-bff-secret)=[^&\s]+/gi;

export function resolveRequestId(header: string | null | undefined): string {
  if (header && REQUEST_ID_PATTERN.test(header)) return header;
  return `req_${crypto.randomUUID()}`;
}

export function normalizedRoute(path: string): string {
  const pathname = path.split("?")[0] ?? "";
  if (
    pathname.length === 0 ||
    pathname.length > 180 ||
    pathname.includes("..") ||
    pathname.includes(" ") ||
    pathname.includes("\\")
  ) {
    return "unmatched";
  }
  return pathname
    .replace(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
      "{id}",
    )
    .replace(/\/\d+(?=\/|$)/g, "/{id}");
}

function parts(key: string): string[] {
  return key
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((part) => part.length > 0);
}

function sensitiveKey(key: string): boolean {
  const keyParts = parts(key);
  if (keyParts.some((part) => SENSITIVE_PARTS.has(part))) return true;
  const collapsed = keyParts.join("");
  if (SENSITIVE_PARTS.has(collapsed) || collapsed === "setcookie") return true;
  return SUFFIXES.some((suffix) => collapsed.endsWith(suffix));
}

function bodyKey(key: string): boolean {
  const keyParts = parts(key);
  const collapsed = keyParts.join("");
  return (
    BODY_PARTS.has(collapsed) || keyParts.some((part) => BODY_PARTS.has(part))
  );
}

export function redactText(value: string): string {
  const cleaned = value.replace(/[\u0000-\u001f\u007f]/g, "");
  const redacted = cleaned.replace(
    new RegExp(SECRET_TEXT.source, "gi"),
    REDACTED,
  );
  return redacted.length > MAX_STRING
    ? redacted.slice(0, MAX_STRING)
    : redacted;
}

export function redactValue(value: unknown, depth = 0): unknown {
  if (depth > MAX_DEPTH) return REDACTED;
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number")
    return Number.isFinite(value) ? value : REDACTED;
  if (typeof value === "string") return redactText(value);
  if (Array.isArray(value)) {
    return value
      .slice(0, MAX_ITEMS)
      .map((item) => redactValue(item, depth + 1));
  }
  if (typeof value === "object") {
    const result: Record<string, unknown> = {};
    let count = 0;
    for (const [key, item] of Object.entries(value)) {
      if (count >= MAX_ITEMS) break;
      count += 1;
      const name = redactText(key).slice(0, 80);
      result[name] =
        sensitiveKey(key) || bodyKey(key)
          ? REDACTED
          : redactValue(item, depth + 1);
    }
    return result;
  }
  return REDACTED;
}

const OUTCOMES = new Set([
  "UPSTREAM_UNAVAILABLE",
  "INVALID_UPSTREAM_RESPONSE",
  "CSRF_FAILED",
  "AUTHENTICATION_REQUIRED",
  "AUTHENTICATION_FAILED",
  "BFF_REQUIRED",
  "ACCESS_TOKEN_EXPIRED",
  "authentication_failed",
  "rate_limited",
  "upstream_error",
  "upstream_unavailable",
]);

export function logBffFailure(event: {
  requestId: string;
  route: string;
  method: string;
  status: number;
  outcome: string;
}): void {
  if (!OUTCOMES.has(event.outcome)) return;
  const payload = redactValue({
    event: "bff.failure",
    service: "web",
    request_id: event.requestId,
    route: normalizedRoute(event.route),
    method: event.method,
    status: event.status,
    outcome: event.outcome,
  });
  const line = JSON.stringify(payload);
  if (!line.includes("\n") && !line.includes("\r")) console.log(line);
}

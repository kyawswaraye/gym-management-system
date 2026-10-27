import { tx } from "../i18n/translate";

export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldErrors;
  readonly requestId: string;
  readonly retryAfter: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    fields: FieldErrors = {},
    requestId = "",
    retryAfter: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

export class NetworkError extends Error {
  readonly status = 0;
  readonly code = "NETWORK";

  constructor(message = tx("You appear to be offline. Check the connection and try again.")) {
    super(message);
    this.name = "NetworkError";
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fieldMessages(value: unknown): FieldErrors {
  if (!isRecord(value)) {
    return {};
  }
  const fields: FieldErrors = {};
  for (const [key, nested] of Object.entries(value)) {
    if (Array.isArray(nested)) {
      fields[key] = nested.map((item) => String(item));
    } else if (typeof nested === "string") {
      fields[key] = [nested];
    } else if (isRecord(nested)) {
      for (const [child, messages] of Object.entries(fieldMessages(nested))) {
        fields[`${key}.${child}`] = messages;
      }
    }
  }
  return fields;
}

export function parseErrorBody(
  text: string,
  status: number,
  retryAfter: string | null,
): ApiError {
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!isRecord(body) || !isRecord(body.error)) {
    return new ApiError(
      status,
      "INVALID_RESPONSE",
      tx("The server response could not be read."),
      {},
      "",
      retryAfter,
    );
  }
  const code =
    typeof body.error.code === "string" ? body.error.code : "INVALID_REQUEST";
  const message =
    typeof body.error.message === "string"
      ? body.error.message
      : tx("The request could not be processed.");
  const requestId = typeof body.request_id === "string" ? body.request_id : "";
  return new ApiError(
    status,
    code,
    message,
    fieldMessages(body.error.fields),
    requestId,
    retryAfter,
  );
}

export type SuccessEnvelope<T> = {
  data: T;
  message: string;
  meta?: { has_more: boolean; next_after: string | null };
};

export function parseSuccessBody<T>(
  text: string,
  status: number,
): SuccessEnvelope<T> {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new ApiError(
      status,
      "INVALID_RESPONSE",
      tx("The server response could not be read."),
    );
  }
  if (!isRecord(body) || body.success === false || !("data" in body)) {
    throw parseErrorBody(text, status, null);
  }
  const message = typeof body.message === "string" ? body.message : "";
  const meta = isRecord(body.meta)
    ? {
        has_more: body.meta.has_more === true,
        next_after:
          typeof body.meta.next_after === "string"
            ? body.meta.next_after
            : null,
      }
    : undefined;
  return { data: body.data as T, message, meta };
}

export function isAccessTokenExpired(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 401 &&
    error.code === "ACCESS_TOKEN_EXPIRED"
  );
}

export function isRevokedSession(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 401 &&
    error.code !== "ACCESS_TOKEN_EXPIRED"
  );
}

export function fieldMessage(
  fields: FieldErrors,
  name: string,
): string | undefined {
  return fields[name]?.[0];
}

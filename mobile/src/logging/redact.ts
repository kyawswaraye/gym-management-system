const SECRET_KEYS = new Set([
  "access_token",
  "refresh_token",
  "payload",
  "push_token",
  "authorization",
  "sha256",
  "url",
]);

export function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redactSecrets(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const output: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(value)) {
    output[key] = SECRET_KEYS.has(key.toLowerCase())
      ? "[redacted]"
      : redactSecrets(nested);
  }
  return output;
}

export function safeDiagnostic(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Something went wrong.";
}

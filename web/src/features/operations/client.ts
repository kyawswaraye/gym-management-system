import { getCsrfToken, mutationHeaders } from "@/features/auth/client";
import type { ApiEnvelope } from "@/types/api";

export async function operationGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api/operations/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error(readError(body));
  return (body as ApiEnvelope<T>).data;
}

export async function operationMutation<T>(
  path: string,
  body: Record<string, unknown>,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/${path}`, {
    method,
    credentials: "same-origin",
    headers: mutationHeaders(csrf),
    body: JSON.stringify(body),
  });
  const responseBody: unknown = await response.json();
  if (!response.ok) throw new Error(readError(responseBody));
  return (responseBody as ApiEnvelope<T>).data;
}

function readError(body: unknown): string {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return "The request could not be completed.";
  }
  const error = (
    body as {
      error?: { message?: unknown; fields?: Record<string, unknown> };
    }
  ).error;
  for (const value of Object.values(error?.fields ?? {})) {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      const message = value.find((item) => typeof item === "string");
      if (typeof message === "string") return message;
    }
  }
  return typeof error?.message === "string"
    ? error.message
    : "The request could not be completed.";
}

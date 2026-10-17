import { getCsrfToken, mutationHeaders } from "@/features/auth/client";
import type {
  ApiEnvelope,
  GymProvisionRequest,
  GymProvisionResult,
} from "@/types/api";

type CollectionEnvelope<T> = ApiEnvelope<T[]> & {
  meta: { has_more?: boolean; next_after?: string | null };
};

async function parseResponse<T>(response: Response): Promise<T> {
  const body: unknown = await response.json();
  if (!response.ok) throw new Error(readError(body));
  return body as T;
}

export async function platformGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api/operations/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body = await parseResponse<ApiEnvelope<T>>(response);
  return body.data;
}

export async function platformGetCollection<T>(
  path: string,
): Promise<CollectionEnvelope<T>> {
  const response = await fetch(`/api/operations/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  return parseResponse<CollectionEnvelope<T>>(response);
}

export async function platformMutation<T>(
  path: string,
  method: "PATCH" | "POST",
  request: Record<string, unknown>,
): Promise<T> {
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/${path}`, {
    method,
    credentials: "same-origin",
    headers: mutationHeaders(csrf),
    body: JSON.stringify(request),
  });
  const body = await parseResponse<ApiEnvelope<T>>(response);
  return body.data;
}

export async function provisionGym(
  request: GymProvisionRequest,
  idempotencyKey: string,
): Promise<GymProvisionResult> {
  const csrf = await getCsrfToken();
  const response = await fetch("/api/operations/admin/gyms", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      ...mutationHeaders(csrf),
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(request),
  });
  const body = await parseResponse<ApiEnvelope<GymProvisionResult>>(response);
  return body.data;
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

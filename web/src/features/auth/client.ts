type ErrorEnvelope = {
  error?: { message?: string };
};

export async function getCsrfToken(): Promise<string> {
  const response = await fetch("/api/auth/csrf", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Unable to prepare a secure request.");
  const body = (await response.json()) as {
    data?: { csrf_token?: string };
  };
  if (!body.data?.csrf_token) {
    throw new Error("Unable to prepare a secure request.");
  }
  return body.data.csrf_token;
}

export async function errorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const body = (await response.json()) as ErrorEnvelope;
    return body.error?.message || fallback;
  } catch {
    return fallback;
  }
}

export function mutationHeaders(csrfToken: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  };
}

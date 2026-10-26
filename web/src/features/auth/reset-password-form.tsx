"use client";

import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { getCsrfToken, mutationHeaders } from "@/features/auth/client";
import { PasswordInput } from "@/features/auth/password-input";

type FieldErrors = Record<string, string>;

function fieldText(value: unknown): string | undefined {
  if (typeof value === "string" && value) return value;
  if (!Array.isArray(value)) return undefined;
  return value.find((item): item is string => typeof item === "string");
}

async function readFailure(
  response: Response,
): Promise<{ message: string; fields: FieldErrors }> {
  const fallback = "Unable to reset the password.";
  try {
    const body = (await response.json()) as {
      error?: { message?: unknown; fields?: unknown };
    };
    const fields: FieldErrors = {};
    if (
      body.error?.fields &&
      typeof body.error.fields === "object" &&
      !Array.isArray(body.error.fields)
    ) {
      for (const [key, value] of Object.entries(body.error.fields)) {
        const message = fieldText(value);
        if (message) fields[key] = message;
      }
    }
    const fieldMessage = Object.values(fields)[0];
    const message =
      fieldMessage ||
      (typeof body.error?.message === "string" && body.error.message
        ? body.error.message
        : fallback);
    return { message, fields };
  } catch {
    return { message: fallback, fields: {} };
  }
}

export function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const passwordId = useId();
  const confirmId = useId();
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldErrors>({});
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") ?? "");
    const confirmation = String(values.get("password_confirmation") ?? "");
    if (password.length < 12 || confirmation.length < 12) {
      setError("Use a password of at least 12 characters.");
      setFields({});
      return;
    }
    if (password !== confirmation) {
      setError("Passwords do not match.");
      setFields({ password_confirmation: "Passwords do not match." });
      return;
    }
    setPending(true);
    setError(null);
    setFields({});
    try {
      const csrf = await getCsrfToken();
      const response = await fetch("/api/auth/password/reset", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({
          reset_token: token,
          password,
          password_confirmation: confirmation,
        }),
      });
      if (!response.ok) {
        const failure = await readFailure(response);
        setError(failure.message);
        setFields(failure.fields);
        return;
      }
      router.replace("/login");
      router.refresh();
    } catch {
      setError(
        await Promise.resolve("The network is unavailable. Please try again."),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" noValidate onSubmit={submit}>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor={passwordId}>New password</label>
        <PasswordInput
          id={passwordId}
          name="password"
          autoComplete="new-password"
          minLength={12}
          required
          disabled={pending}
          aria-invalid={fields.password ? true : undefined}
        />
        {fields.password ? (
          <span className="error">{fields.password}</span>
        ) : null}
      </div>
      <div className="field">
        <label htmlFor={confirmId}>Confirm password</label>
        <PasswordInput
          id={confirmId}
          name="password_confirmation"
          autoComplete="new-password"
          minLength={12}
          required
          disabled={pending}
          aria-invalid={fields.password_confirmation ? true : undefined}
        />
        {fields.password_confirmation ? (
          <span className="error">{fields.password_confirmation}</span>
        ) : null}
      </div>
      <button className="button button-edit" type="submit" disabled={pending}>
        {pending ? "Updating…" : "Update password"}
      </button>
    </form>
  );
}

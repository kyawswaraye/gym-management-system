"use client";
import { useTx } from "@/i18n/use-tx";

import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import {
  authenticatedHomePath,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";
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
  const fallback = "Unable to accept invitation.";
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
    if (!fields.password && fields.non_field_errors) {
      fields.password = fields.non_field_errors;
    }
    const fieldMessage = fields.password || Object.values(fields)[0];
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

export function OwnerInvitationForm({ token }: { token: string }) {
  const tx = useTx();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldErrors>({});
  const [pending, setPending] = useState(false);
  const passwordId = useId();
  const confirmId = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") ?? "");
    const confirmation = String(values.get("password_confirmation") ?? "");
    if (password.length < 12 || confirmation.length < 12) {
      const message = "Use a password of at least 12 characters.";
      const next: FieldErrors = {};
      if (password.length < 12) next.password = message;
      if (confirmation.length < 12) next.password_confirmation = message;
      setError(message);
      setFields(next);
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
      const response = await fetch("/api/auth/accept-owner-invitation", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({
          invitation_token: token,
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
      router.replace(await authenticatedHomePath());
      router.refresh();
    } catch {
      setError(tx("The network is unavailable. Please try again."));
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" noValidate onSubmit={submit}>
      {error && (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      )}
      <div className="field">
        <label htmlFor={passwordId}>{tx("Password")}</label>
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
          <span className="error">{tx(fields.password)}</span>
        ) : null}
      </div>
      <div className="field">
        <label htmlFor={confirmId}>{tx("Confirm password")}</label>
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
          <span className="error">{tx(fields.password_confirmation)}</span>
        ) : null}
      </div>
      <button className="button" disabled={pending}>
        {pending ? tx("Accepting…") : tx("Accept Owner invitation")}
      </button>
    </form>
  );
}

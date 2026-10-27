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
import type { StaffAcceptInput } from "@/types/api";

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

function optionalText(
  value: string,
  label: string,
  max: number,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string | null {
  if (!value) return null;
  if (value.length > max) {
    throw new Error(
      tx("Enter a {label} up to {max} characters.", {
        label: tx(label),
        max,
      }),
    );
  }
  return value;
}

function Field({
  label,
  name,
  type,
  required = false,
  autoComplete,
  error,
  minLength,
  maxLength,
}: {
  label: string;
  name: string;
  type: string;
  required?: boolean;
  autoComplete: string;
  error?: string | undefined;
  minLength?: number;
  maxLength?: number;
}) {
  const tx = useTx();
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label htmlFor={id}>{tx(label)}</label>
      {type === "password" ? (
        <PasswordInput
          id={id}
          name={name}
          required={required}
          autoComplete={autoComplete}
          minLength={minLength}
          maxLength={maxLength}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
      ) : (
        <input
          id={id}
          className="input"
          name={name}
          type={type}
          required={required}
          autoComplete={autoComplete}
          minLength={minLength}
          maxLength={maxLength}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
      )}
      {error ? (
        <span id={errorId} className="error">
          {tx(error)}
        </span>
      ) : null}
    </div>
  );
}

export function StaffInvitationForm({ token }: { token: string }) {
  const tx = useTx();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldErrors>({});
  const [pending, setPending] = useState(false);

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
      setError(tx("Passwords do not match."));
      setFields({ password_confirmation: "Passwords do not match." });
      return;
    }
    let optional: Pick<
      StaffAcceptInput,
      "first_name" | "last_name" | "phone" | "display_name"
    >;

    try {
      const firstName = optionalText(
        String(values.get("first_name") ?? "").trim(),
        "First name",
        150,
        tx,
      );
      const lastName = optionalText(
        String(values.get("last_name") ?? "").trim(),
        "Last name",
        150,
        tx,
      );
      const phone = optionalText(
        String(values.get("phone") ?? "").trim(),
        "Phone",
        32,
        tx,
      );
      const displayName = optionalText(
        String(values.get("display_name") ?? "").trim(),
        "Display name",
        150,
        tx,
      );
      optional = {
        ...(firstName ? { first_name: firstName } : {}),
        ...(lastName ? { last_name: lastName } : {}),
        ...(phone ? { phone } : {}),
        ...(displayName ? { display_name: displayName } : {}),
      };
    } catch (failure) {
      setFields({});
      setError(
        failure instanceof Error
          ? failure.message
          : "Check the confirmation fields.",
      );
      return;
    }

    const body: StaffAcceptInput = {
      invitation_token: token,
      password,
      password_confirmation: confirmation,
      ...optional,
    };
    setPending(true);
    setError(null);
    setFields({});
    try {
      const csrf = await getCsrfToken();
      const response = await fetch("/api/auth/accept-staff-invitation", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const failure = await readFailure(response);
        setError(failure.message);
        setFields(failure.fields);
        return;
      }
      router.replace(await authenticatedHomePath());
      router.refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "The network is unavailable. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" noValidate onSubmit={submit}>
      {error ? (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      ) : null}
      <Field
        label="Password"
        name="password"
        type="password"
        required
        autoComplete="new-password"
        minLength={12}
        error={fields.password}
      />

      <Field
        label="Confirm password"
        name="password_confirmation"
        type="password"
        required
        autoComplete="new-password"
        minLength={12}
        error={fields.password_confirmation}
      />

      <Field
        label="First name"
        name="first_name"
        type="text"
        autoComplete="given-name"
        maxLength={150}
        error={fields.first_name}
      />

      <Field
        label="Last name"
        name="last_name"
        type="text"
        autoComplete="family-name"
        maxLength={150}
        error={fields.last_name}
      />

      <Field
        label="Phone"
        name="phone"
        type="tel"
        autoComplete="tel"
        maxLength={32}
        error={fields.phone}
      />

      <Field
        label="Display name"
        name="display_name"
        type="text"
        autoComplete="nickname"
        maxLength={150}
        error={fields.display_name}
      />

      <button className="button" disabled={pending}>
        {pending ? tx("Accepting…") : tx("Accept staff invitation")}
      </button>
    </form>
  );
}

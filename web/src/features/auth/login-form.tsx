"use client";

import { useId, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  authenticatedHomePath,
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";
import { PasswordInput } from "@/features/auth/password-input";

export function LoginForm({
  sessionExpired = false,
}: {
  sessionExpired?: boolean;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(
    sessionExpired ? "Your session expired. Please sign in again." : null,
  );
  const [pending, setPending] = useState(false);
  const [mfaRequired, setMfaRequired] = useState(false);
  const emailId = useId();
  const passwordId = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const code = String(form.get("code") ?? "").trim();
    if (mfaRequired && !/^\d{6}$/.test(code)) {
      setError("Enter the six-digit authenticator code.");
      return;
    }
    if (!mfaRequired && (!email || !password)) {
      setError("Enter both your email and password.");
      return;
    }

    setPending(true);
    try {
      const csrf = await getCsrfToken();
      const response = await fetch(
        mfaRequired ? "/api/auth/mfa/challenge" : "/api/auth/login",
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders(csrf),
          body: JSON.stringify(
            mfaRequired
              ? { code }
              : { email, password, device_name: "Web browser" },
          ),
        },
      );
      if (!response.ok) {
        setError(await errorMessage(response, "Sign-in failed."));
        return;
      }
      const body = (await response.json()) as {
        data?: { requires_gym_selection?: boolean; requires_mfa?: boolean };
      };
      if (body.data?.requires_mfa) {
        setMfaRequired(true);
        return;
      }
      const destination = body.data?.requires_gym_selection
        ? "/select-gym"
        : await authenticatedHomePath();
      router.replace(destination);
      router.refresh();
    } catch {
      setError("The network is unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!mfaRequired && (
        <div className="field">
          <label htmlFor={emailId}>Email</label>
          <input
            id={emailId}
            className="input"
            name="email"
            type="email"
            autoComplete="username"
            required
            disabled={pending}
          />
        </div>
      )}
      {!mfaRequired && (
        <div className="field">
          <label htmlFor={passwordId}>Password</label>
          <PasswordInput
            id={passwordId}
            name="password"
            autoComplete="current-password"
            required
            disabled={pending}
          />
        </div>
      )}
      {mfaRequired && (
        <label className="field">
          <span>Authenticator code</span>
          <input
            className="input"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            required
            disabled={pending}
          />
        </label>
      )}
      <button className="button" type="submit" disabled={pending}>
        {pending ? "Verifying…" : mfaRequired ? "Verify code" : "Sign in"}
      </button>
      {!mfaRequired ? (
        <p className="muted">
          <Link href="/forgot-password">Forgot password</Link>
        </p>
      ) : null}
    </form>
  );
}

"use client";
import { useTx } from "@/i18n/use-tx";

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
import { DigitInput } from "@/lib/ui/digit-input";

export function LoginForm({
  sessionExpired = false,
}: {
  sessionExpired?: boolean;
}) {
  const tx = useTx();
  const router = useRouter();
  const [error, setError] = useState(
    sessionExpired ? tx("Your session expired. Please sign in again.") : null,
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
      setError(tx("Enter the six-digit authenticator code."));
      return;
    }
    if (!mfaRequired && (!email || !password)) {
      setError(tx("Enter both your email and password."));
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
        setError(await errorMessage(response, tx("Sign-in failed.")));
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
      setError(tx("The network is unavailable. Please try again."));
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
      {error && (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      )}
      {!mfaRequired && (
        <div className="field">
          <label htmlFor={emailId}>{tx("Email")}</label>
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
          <label htmlFor={passwordId}>{tx("Password")}</label>
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
          <span>{tx("Authenticator code")}</span>
          <DigitInput
            autoComplete="one-time-code"
            className="input"
            disabled={pending}
            maxLength={6}
            mode={{ kind: "code" }}
            name="code"
            pattern="[0-9]{6}"
            required
          />
        </label>
      )}
      <button className="button" type="submit" disabled={pending}>
        {pending
          ? tx("Verifying…")
          : mfaRequired
            ? tx("Verify code")
            : tx("Sign in")}
      </button>
      {!mfaRequired ? (
        <p className="muted">
          <Link href="/forgot-password">{tx("Forgot password")}</Link>
        </p>
      ) : null}
    </form>
  );
}

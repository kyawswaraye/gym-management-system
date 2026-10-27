"use client";
import { useTx } from "@/i18n/use-tx";

import { useState, type FormEvent } from "react";

import {
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";

export function ForgotPasswordForm() {
  const tx = useTx();
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    if (!email) {
      setError(tx("Enter the email for your account."));
      return;
    }
    setPending(true);
    try {
      const csrf = await getCsrfToken();
      const response = await fetch("/api/auth/password/forgot", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ email }),
      });
      if (!response.ok) {
        setError(
          await errorMessage(response, tx("Unable to send a reset link.")),
        );
        return;
      }
      setSent(true);
    } catch {
      setError(tx("The network is unavailable. Please try again."));
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <p className="mt-6" role="status">
        {" "}
        {tx(
          "If an account exists for that email, a reset link will be sent.",
        )}{" "}
      </p>
    );
  }

  return (
    <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
      {error ? (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      ) : null}
      <label className="field">
        <span>{tx("Email")}</span>
        <input
          className="input"
          name="email"
          type="email"
          autoComplete="username"
          required
          disabled={pending}
        />
      </label>
      <button className="button" type="submit" disabled={pending}>
        {pending ? tx("Sending…") : tx("Send reset link")}
      </button>
    </form>
  );
}

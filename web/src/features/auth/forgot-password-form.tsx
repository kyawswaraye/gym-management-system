"use client";

import { useState, type FormEvent } from "react";

import {
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";

export function ForgotPasswordForm() {
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    if (!email) {
      setError("Enter the email for your account.");
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
        setError(await errorMessage(response, "Unable to send a reset link."));
        return;
      }
      setSent(true);
    } catch {
      setError("The network is unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <p className="mt-6" role="status">
        If an account exists for that email, a reset link will be sent.
      </p>
    );
  }

  return (
    <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <label className="field">
        <span>Email</span>
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
        {pending ? "Sending…" : "Send reset link"}
      </button>
    </form>
  );
}

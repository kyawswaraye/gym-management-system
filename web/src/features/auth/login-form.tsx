"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import {
  authenticatedHomePath,
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";

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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    if (!email || !password) {
      setError("Enter both your email and password.");
      return;
    }

    setPending(true);
    try {
      const csrf = await getCsrfToken();
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ email, password, device_name: "Web browser" }),
      });
      if (!response.ok) {
        setError(await errorMessage(response, "Sign-in failed."));
        return;
      }
      const body = (await response.json()) as {
        data?: { requires_gym_selection?: boolean };
      };
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
      <label className="field">
        <span>Password</span>
        <input
          className="input"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          disabled={pending}
        />
      </label>
      <button className="button" type="submit" disabled={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

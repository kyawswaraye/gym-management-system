"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import {
  authenticatedHomePath,
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";

export function OwnerInvitationForm({ token }: { token: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") ?? "");
    const confirmation = String(values.get("password_confirmation") ?? "");
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);
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
        setError(await errorMessage(response, "Unable to accept invitation."));
        return;
      }
      router.replace(await authenticatedHomePath());
      router.refresh();
    } catch {
      setError("The network is unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="mt-6 grid gap-4" onSubmit={submit}>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <label className="field">
        <span>Password</span>
        <input
          className="input"
          name="password"
          type="password"
          minLength={12}
          required
        />
      </label>
      <label className="field">
        <span>Confirm password</span>
        <input
          className="input"
          name="password_confirmation"
          type="password"
          minLength={12}
          required
        />
      </label>
      <button className="button" disabled={pending}>
        {pending ? "Accepting…" : "Accept Owner invitation"}
      </button>
    </form>
  );
}

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import {
  authenticatedHomePath,
  errorMessage,
  getCsrfToken,
  mutationHeaders,
} from "@/features/auth/client";
import type { GymSummary } from "@/types/api";

function membershipId(gym: GymSummary): string {
  return gym.gym_membership_id ?? gym.id ?? "";
}

function gymName(gym: GymSummary): string {
  return gym.gym?.name ?? gym.display_name ?? gym.name ?? "Authorized gym";
}

export function GymSelectionForm({ gyms }: { gyms: GymSummary[] }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const gymMembershipId = String(form.get("gym_membership_id") ?? "");
    if (!gymMembershipId) {
      setError("Choose a gym.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const csrf = await getCsrfToken();
      const response = await fetch("/api/auth/select-gym", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ gym_membership_id: gymMembershipId }),
      });
      if (!response.ok) {
        if (response.status === 401) {
          router.replace("/login?reason=expired");
          return;
        }
        setError(await errorMessage(response, "Unable to select this gym."));
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
    <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {gyms.length ? (
        <>
          <fieldset className="grid gap-3">
            <legend className="mb-2 font-bold">Available gyms</legend>
            {gyms.map((gym) => {
              const id = membershipId(gym);
              return (
                <label
                  key={id}
                  className="flex min-h-12 cursor-pointer items-center gap-3 rounded-lg border border-slate-300 p-3"
                >
                  <input
                    name="gym_membership_id"
                    type="radio"
                    value={id}
                    required
                    disabled={pending}
                  />
                  <span>{gymName(gym)}</span>
                </label>
              );
            })}
          </fieldset>
          <button className="button" type="submit" disabled={pending}>
            {pending ? "Opening gym…" : "Continue"}
          </button>
        </>
      ) : (
        <p className="muted">
          No active gym memberships are available for this account.
        </p>
      )}
    </form>
  );
}

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { OwnerInvitation, OwnerItem } from "@/types/api";

export function OwnersManager({
  currentMembershipId,
  mfaRecent,
}: {
  currentMembershipId: string;
  mfaRecent: boolean;
}) {
  const [owners, setOwners] = useState<OwnerItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    setOwners(await operationGet<OwnerItem[]>("owners"));
  }, []);

  useEffect(() => {
    let active = true;
    void operationGet<OwnerItem[]>("owners")
      .then((items) => {
        if (active) setOwners(items);
      })
      .catch((failure: unknown) => {
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "Unable to load Owners.",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setPending(true);
    setError(null);
    try {
      const result = await operationMutation<OwnerInvitation>(
        "owners/invitations",
        {
          email: String(values.get("email") ?? "").trim(),
          first_name: String(values.get("first_name") ?? "").trim(),
          last_name: String(values.get("last_name") ?? "").trim(),
          phone: String(values.get("phone") ?? "").trim(),
          send_invitation: true,
        },
      );
      setSuccess(`Invitation sent to ${result.email}.`);
      form.reset();
      await load();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Unable to invite Owner.",
      );
    } finally {
      setPending(false);
    }
  }

  async function revoke(owner: OwnerItem) {
    const reason = globalThis.prompt("Reason for revoking this Owner:");
    if (!reason || reason.trim().length < 3) {
      setError("A reason of at least 3 characters is required.");
      return;
    }
    if (!globalThis.confirm(`Revoke Owner access for ${owner.email}?`)) return;
    setPending(true);
    setError(null);
    try {
      await operationMutation<OwnerItem>(
        `owners/${owner.gym_membership_id}/revoke`,
        { reason: reason.trim() },
      );
      setSuccess(`${owner.email} was revoked.`);
      await load();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Unable to revoke Owner.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <section className="card">
        <h2 className="text-xl font-bold">Invite Owner</h2>
        {!mfaRecent ? (
          <p className="mt-4 text-sm">
            Recent authenticator verification is required.{" "}
            <Link className="text-blue-700 hover:underline" href="/mfa-setup">
              Set up or verify MFA
            </Link>
          </p>
        ) : (
          <form className="mt-4 grid gap-3" onSubmit={invite}>
            <label className="field">
              <span>Email</span>
              <input className="input" name="email" type="email" required />
            </label>
            <label className="field">
              <span>First name</span>
              <input className="input" name="first_name" required />
            </label>
            <label className="field">
              <span>Last name</span>
              <input className="input" name="last_name" required />
            </label>
            <label className="field">
              <span>Phone</span>
              <input className="input" name="phone" />
            </label>
            <button className="button" disabled={pending}>
              Send invitation
            </button>
          </form>
        )}
      </section>
      <section className="card">
        <h2 className="text-xl font-bold">Owners</h2>
        {error && (
          <p className="error mt-4" role="alert">
            {error}
          </p>
        )}
        {success && (
          <p className="mt-4 text-sm text-emerald-700" role="status">
            {success}
          </p>
        )}
        <ul className="mt-4 grid gap-3">
          {owners.map((owner) => (
            <li
              className="rounded-lg border border-slate-200 p-3"
              key={owner.gym_membership_id}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <strong>
                    {owner.first_name} {owner.last_name}
                  </strong>
                  <p className="muted text-sm">
                    {owner.email} · {owner.status}
                  </p>
                </div>
                {mfaRecent &&
                  owner.status === "ACTIVE" &&
                  owner.gym_membership_id !== currentMembershipId && (
                    <button
                      className="button"
                      type="button"
                      disabled={pending}
                      onClick={() => void revoke(owner)}
                    >
                      Revoke
                    </button>
                  )}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

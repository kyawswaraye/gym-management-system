"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { FormDialog } from "@/components/form-dialog";
import { operationGet, operationMutation } from "@/features/operations/client";
import type { OwnerInvitation, OwnerItem } from "@/types/api";

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Active",
  INVITED: "Invited",
  SUSPENDED: "Suspended",
  REVOKED: "Revoked",
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function statusTone(status: string): string {
  switch (status) {
    case "ACTIVE":
      return "bg-emerald-50 text-emerald-700 ring-emerald-600/15";
    case "INVITED":
      return "bg-sky-50 text-sky-700 ring-sky-600/15";
    case "SUSPENDED":
      return "bg-amber-50 text-amber-800 ring-amber-600/15";
    case "REVOKED":
      return "bg-rose-50 text-rose-700 ring-rose-600/15";
    default:
      return "bg-slate-100 text-slate-600 ring-slate-500/10";
  }
}

function initials(owner: OwnerItem): string {
  const letters = [owner.first_name, owner.last_name]
    .map((part) => part.trim().charAt(0).toUpperCase())
    .filter(Boolean);
  return letters.join("") || "?";
}

function displayName(owner: OwnerItem): string {
  const name = `${owner.first_name} ${owner.last_name}`.trim();
  return name || owner.email;
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

function formatWhen(
  value: string | null,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const month = MONTHS[date.getMonth()];
  if (!month) return null;
  return `${date.getDate()} ${tx(month)} ${date.getFullYear()}`;
}

function accessNote(
  owner: OwnerItem,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string | null {
  if (owner.status === "ACTIVE") {
    const when = formatWhen(owner.activated_at, tx);
    return when ? tx("Since {when}", { when }) : null;
  }
  if (owner.status === "INVITED") {
    const when = formatWhen(owner.invited_at, tx);
    return when ? tx("Sent {when}", { when }) : null;
  }
  return formatWhen(owner.activated_at ?? owner.invited_at, tx);
}

function listSummary(
  owners: OwnerItem[],
  loading: boolean,
  failed: boolean,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  if (loading) return tx("Fetching the latest list.");
  if (owners.length === 0) {
    return failed
      ? tx("The list is unavailable.")
      : tx("No one on this list yet.");
  }
  return countLine(owners, tx);
}

function countLine(
  owners: OwnerItem[],
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  const people =
    owners.length === 1
      ? tx("1 person")
      : tx("{count} people", { count: owners.length });
  const groups = ["ACTIVE", "INVITED", "SUSPENDED", "REVOKED"].flatMap(
    (status) => {
      const count = owners.filter((owner) => owner.status === status).length;
      return count > 0
        ? [tx("{count} {status}", { count, status: tx(statusLabel(status)) })]
        : [];
    },
  );
  return [people, ...groups].join(" · ");
}

function orderedOwners(
  owners: OwnerItem[],
  currentMembershipId: string,
): OwnerItem[] {
  const rank = (owner: OwnerItem) => {
    if (owner.gym_membership_id === currentMembershipId) return 0;
    if (owner.status === "ACTIVE") return 1;
    if (owner.status === "INVITED") return 2;
    if (owner.status === "SUSPENDED") return 3;
    return 4;
  };
  return owners
    .map((owner, index) => ({ owner, index }))
    .sort(
      (left, right) =>
        rank(left.owner) - rank(right.owner) || left.index - right.index,
    )
    .map((item) => item.owner);
}

function failureMessage(failure: unknown, fallback: string): string {
  return failure instanceof Error && failure.message
    ? failure.message
    : fallback;
}

export function OwnersManager({
  currentMembershipId,
  mfaRecent,
}: {
  currentMembershipId: string;
  mfaRecent: boolean;
}) {
  const tx = useTx();
  const [owners, setOwners] = useState<OwnerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<OwnerItem | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<OwnerItem | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const pendingRef = useRef(false);

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
        if (active) setError(failureMessage(failure, "Unable to load owners."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    setSuccess(null);
    setInviteError(null);
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
      setSuccess(tx("Invitation sent to {email}.", { email: result.email }));
      setInviting(false);
      form.reset();
      try {
        await load();
      } catch (failure) {
        setError(failureMessage(failure, "Unable to load owners."));
      }
    } catch (failure) {
      setInviteError(failureMessage(failure, "Unable to invite owner."));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  async function revoke(owner: OwnerItem, reason: string) {
    const trimmed = reason.trim();
    if (pendingRef.current || trimmed.length < 3) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    setSuccess(null);
    setRevokeError(null);
    try {
      await operationMutation<OwnerItem>(
        `owners/${owner.gym_membership_id}/revoke`,
        { reason: trimmed },
      );
      setSuccess(
        tx("{email} no longer has owner access.", { email: owner.email }),
      );
      setRevokeTarget(null);
      try {
        await load();
      } catch (failure) {
        setError(failureMessage(failure, "Unable to load owners."));
      }
    } catch (failure) {
      setRevokeError(failureMessage(failure, "Unable to revoke owner."));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  async function removeInvitation(owner: OwnerItem) {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    setSuccess(null);
    setRemoveError(null);
    try {
      await operationMutation<OwnerItem>(
        `owners/${owner.gym_membership_id}/cancel-invitation`,
        {},
      );
      setSuccess(
        tx("{email} was removed from the owner list.", { email: owner.email }),
      );
      setRemoveTarget(null);
      try {
        await load();
      } catch (failure) {
        setError(failureMessage(failure, "Unable to load owners."));
      }
    } catch (failure) {
      setRemoveError(
        failureMessage(failure, "Unable to remove the invitation."),
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  const listed = orderedOwners(owners, currentMembershipId);

  return (
    <div className="grid gap-5">
      {!mfaRecent ? (
        <div className="flex flex-col gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-amber-950">
              {" "}
              {tx("Authenticator check needed")}{" "}
            </p>
            <p className="mt-0.5 text-sm leading-6 text-amber-900/80">
              {" "}
              {tx(
                "Invite and revoke stay locked until you verify with your authenticator.",
              )}{" "}
            </p>
          </div>
          <Link className="button w-full sm:w-fit" href="/mfa-setup">
            {" "}
            {tx("Set up or verify MFA")}{" "}
          </Link>
        </div>
      ) : null}
      {error ? (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      ) : null}
      {success ? (
        <p
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
          role="status"
        >
          {tx(success)}
        </p>
      ) : null}

      <section
        aria-labelledby="owner-list-title"
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="flex flex-col gap-3 border-b border-slate-100 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div>
            <h2
              className="text-lg font-bold text-slate-900"
              id="owner-list-title"
            >
              {" "}
              {tx("Owner list")}{" "}
            </h2>
            <p className="mt-0.5 text-sm text-slate-500">
              {listSummary(owners, loading, Boolean(error), tx)}
            </p>
          </div>
          {mfaRecent ? (
            <button
              className="button button-create w-full sm:w-fit"
              onClick={() => {
                setError(null);
                setSuccess(null);
                setInviteError(null);
                setInviting(true);
              }}
              type="button"
            >
              {" "}
              {tx("Invite owner")}{" "}
            </button>
          ) : null}
        </div>

        {loading ? (
          <div className="grid gap-3 p-5">
            <p className="text-sm font-medium text-slate-500" role="status">
              {" "}
              {tx("Loading owners.")}{" "}
            </p>
            {[0, 1].map((item) => (
              <div
                className="h-16 animate-pulse rounded-xl bg-slate-100"
                key={item}
              />
            ))}
          </div>
        ) : null}

        {!loading && owners.length === 0 && !error ? (
          <div className="px-6 py-16 text-center" role="status">
            <p className="font-semibold text-slate-800">
              {" "}
              {tx("No owners are listed.")}{" "}
            </p>
            <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
              {mfaRecent
                ? tx("Invite someone who should run this gym with you.")
                : tx("Owners appear here after an invitation is accepted.")}
            </p>
          </div>
        ) : null}

        {!loading && owners.length === 0 && error ? (
          <p className="px-6 py-12 text-center text-sm text-slate-500">
            {" "}
            {tx("The owner list could not be loaded.")}{" "}
          </p>
        ) : null}

        {listed.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {listed.map((owner) => {
              const isYou = owner.gym_membership_id === currentMembershipId;
              const note = accessNote(owner, tx);
              const canRevoke =
                mfaRecent && owner.status === "ACTIVE" && !isYou;
              const canRemove =
                mfaRecent && owner.status === "INVITED" && !isYou;
              return (
                <li className="px-4 py-4 sm:px-5" key={owner.gym_membership_id}>
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      aria-hidden="true"
                      className={`grid size-11 shrink-0 place-items-center rounded-xl text-sm font-black ${
                        isYou
                          ? "bg-emerald-600 text-white"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {initials(owner)}
                    </span>
                    <div className="min-w-0 flex-1 basis-52">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate font-semibold text-slate-900">
                          {displayName(owner)}
                        </p>
                        {isYou ? (
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[0.65rem] font-bold tracking-wide text-slate-600 uppercase">
                            {" "}
                            {tx("You")}{" "}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-0.5 truncate text-sm text-slate-500">
                        {owner.email}
                      </p>
                      {owner.phone ? (
                        <p className="mt-0.5 truncate text-xs text-slate-400">
                          {owner.phone}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 max-sm:basis-full max-sm:pl-14 sm:ml-auto sm:justify-end">
                      {note ? (
                        <p className="text-xs font-medium text-slate-400">
                          {note}
                        </p>
                      ) : null}
                      <StatusBadge status={owner.status} />
                      {canRevoke ? (
                        <button
                          className="button button-delete button-sm"
                          disabled={pending}
                          onClick={() => {
                            if (pendingRef.current) return;
                            setRevokeError(null);
                            setRevokeTarget(owner);
                          }}
                          type="button"
                        >
                          {" "}
                          {tx("Revoke")}{" "}
                        </button>
                      ) : null}
                      {canRemove ? (
                        <button
                          className="button button-delete button-sm"
                          disabled={pending}
                          onClick={() => {
                            if (pendingRef.current) return;
                            setRemoveError(null);
                            setRemoveTarget(owner);
                          }}
                          type="button"
                        >
                          {" "}
                          {tx("Remove")}{" "}
                        </button>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      {mfaRecent && inviting ? (
        <FormDialog
          compact
          description="They receive the same authority as every other owner."
          onClose={() => {
            if (!pending) setInviting(false);
          }}
          onSubmit={invite}
          pending={pending}
          title={tx("Invite owner")}
        >
          {inviteError ? (
            <p className="error" role="alert">
              {inviteError}
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField
              autoComplete="email"
              className="sm:col-span-2"
              label="Email"
              maxLength={254}
              name="email"
              required
              type="email"
            />
            <TextField
              autoComplete="given-name"
              label="First name"
              maxLength={150}
              name="first_name"
              required
            />
            <TextField
              autoComplete="family-name"
              label="Last name"
              maxLength={150}
              name="last_name"
              required
            />
            <TextField
              autoComplete="tel"
              className="sm:col-span-2"
              label="Phone"
              maxLength={32}
              name="phone"
              optional
              type="tel"
            />
          </div>
          <div>
            <button
              className="button button-create"
              disabled={pending}
              type="submit"
            >
              {pending ? tx("Sending…") : tx("Send invitation")}
            </button>
          </div>
        </FormDialog>
      ) : null}

      {revokeTarget ? (
        <RevokeOwnerDialog
          email={revokeTarget.email}
          onCancel={() => {
            if (!pending) setRevokeTarget(null);
          }}
          onConfirm={(reason) => void revoke(revokeTarget, reason)}
          pending={pending}
          serverError={revokeError}
        />
      ) : null}
      {removeTarget ? (
        <RemoveInvitationDialog
          email={removeTarget.email}
          onCancel={() => {
            if (!pending) setRemoveTarget(null);
          }}
          onConfirm={() => void removeInvitation(removeTarget)}
          pending={pending}
          serverError={removeError}
        />
      ) : null}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${statusTone(status)}`}
    >
      {tx(statusLabel(status))}
    </span>
  );
}

function TextField({
  label,
  name,
  type = "text",
  required = false,
  optional = false,
  maxLength,
  autoComplete,
  className,
}: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  optional?: boolean;
  maxLength?: number;
  autoComplete?: string;
  className?: string;
}) {
  const tx = useTx();
  const id = useId();
  return (
    <div className={className ? `field ${className}` : "field"}>
      <label className="text-sm font-semibold text-slate-700" htmlFor={id}>
        {tx(label)}
        {optional ? (
          <span aria-hidden="true" className="font-medium text-slate-400">
            {" "}
            {tx("· Optional")}{" "}
          </span>
        ) : null}
      </label>
      <input
        autoComplete={autoComplete}
        className="input"
        id={id}
        maxLength={maxLength}
        name={name}
        required={required}
        type={type}
      />
    </div>
  );
}

function RevokeOwnerDialog({
  email,
  pending,
  serverError,
  onCancel,
  onConfirm,
}: {
  email: string;
  pending: boolean;
  serverError: string | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const tx = useTx();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = reason.trim();
    if (trimmed.length < 3) {
      setError(tx("A reason of at least 3 characters is required."));
      return;
    }
    setError("");
    onConfirm(trimmed);
  }

  return (
    <FormDialog
      compact
      description="They lose access to this gym. The reason is stored with the audit record."
      onClose={onCancel}
      onSubmit={submit}
      pending={pending}
      title={tx("Revoke {email}?", { email })}
    >
      <div className="field">
        <label
          className="text-sm font-semibold text-slate-700"
          htmlFor={reasonId}
        >
          {" "}
          {tx("Reason")}{" "}
        </label>
        <textarea
          className="input min-h-28"
          id={reasonId}
          maxLength={500}
          onChange={(event) => setReason(event.target.value)}
          value={reason}
        />
        <p className="text-xs leading-5 text-slate-500">
          {" "}
          {tx("At least 3 characters.")}{" "}
        </p>
      </div>
      {error || serverError ? (
        <p className="error" role="alert">
          {error || serverError}
        </p>
      ) : null}
      <div>
        <button
          className="button button-delete"
          disabled={pending}
          type="submit"
        >
          {pending ? tx("Revoking…") : tx("Revoke owner")}
        </button>
      </div>
    </FormDialog>
  );
}

function RemoveInvitationDialog({
  email,
  pending,
  serverError,
  onCancel,
  onConfirm,
}: {
  email: string;
  pending: boolean;
  serverError: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const tx = useTx();
  return (
    <FormDialog
      compact
      description="The invitation link stops working, and this person leaves the list."
      onClose={onCancel}
      onSubmit={(event) => {
        event.preventDefault();
        onConfirm();
      }}
      pending={pending}
      title={tx("Remove {email}?", { email })}
    >
      {serverError ? (
        <p className="error" role="alert">
          {serverError}
        </p>
      ) : null}
      <div>
        <button
          className="button button-delete"
          disabled={pending}
          type="submit"
        >
          {pending ? tx("Removing…") : tx("Remove invitation")}
        </button>
      </div>
    </FormDialog>
  );
}

"use client";

import { useEffect, useState, type FormEvent } from "react";

import { listStaff, scanTrainerCheckIn } from "@/features/operations/client";
import { listAssignedMembers, listTrainers } from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { collectLookupPages } from "@/features/pt/lookup";
import { Field, Loading, TextInput } from "@/features/finance/ui";
import type { AssignedMember } from "@/types/api";

function label(member: AssignedMember): string {
  return (
    `${member.first_name} ${member.last_name}`.trim() || member.member_number
  );
}

async function resolveProfile(membershipId: string): Promise<string> {
  const trainers = await collectLookupPages((after) =>
    listTrainers({ limit: "100", ...(after ? { after } : {}) }),
  );
  if (
    !trainers.bounded &&
    !trainers.error &&
    trainers.items.length === 1 &&
    trainers.items[0]
  ) {
    return trainers.items[0].id;
  }
  let after: string | undefined;
  const seen = new Set<string>();
  for (let page = 0; page < 20; page += 1) {
    const staff = await listStaff({
      status: "ACTIVE",
      limit: 100,
      ...(after ? { after } : {}),
    });
    const match = staff.data.find(
      (row) => row.membership.gym_membership_id === membershipId,
    );
    if (match) return match.id;
    const next = staff.meta.next_after;
    if (!staff.meta.has_more || !next || seen.has(next)) break;
    seen.add(next);
    after = next;
  }
  if (trainers.error) throw new Error(trainers.error);
  throw new Error(
    "The trainer profile for this membership could not be resolved.",
  );
}

export function TrainerScan({ membershipId }: { membershipId: string }) {
  const [profileId, setProfileId] = useState("");
  const [members, setMembers] = useState<AssignedMember[]>([]);
  const [query, setQuery] = useState("");
  const [memberId, setMemberId] = useState("");
  const [payload, setPayload] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void resolveProfile(membershipId)
      .then(async (id) => {
        if (!active) return;
        setProfileId(id);
        const collected = await collectLookupPages((after) =>
          listAssignedMembers(id, {
            limit: "100",
            ...(after ? { after } : {}),
          }),
        );
        if (!active) return;
        setMembers(
          collected.error && !collected.bounded ? [] : collected.items,
        );
        if (collected.error) setError(collected.error);
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            phase5Error(caught, "Assigned members could not be loaded."),
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [membershipId]);

  const visible = members.filter((member) => {
    const haystack = `${label(member)} ${member.member_number}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!memberId || pending) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      await scanTrainerCheckIn({ memberId, payload });
      setPayload("");
      setNotice("Checked in from the trainer code.");
    } catch (caught) {
      setError(phase5Error(caught, "The check-in code is not valid."));
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="trainer-scan-title" className="grid gap-3">
      <h2 className="text-xl font-semibold" id="trainer-scan-title">
        Trainer QR scan
      </h2>
      <p className="muted">
        Choose an assigned member and accept the scanner text or paste the code.
        The code is sent once and is not kept in this browser.
      </p>
      {loading ? <Loading label="Loading assigned members." /> : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <form className="grid gap-3" onSubmit={(event) => void submit(event)}>
        <Field label="Search assigned members">
          <TextInput
            onChange={(event) => setQuery(event.target.value)}
            value={query}
          />
        </Field>
        <Field label="Assigned member">
          <select
            className="input"
            onChange={(event) => setMemberId(event.target.value)}
            required
            value={memberId}
          >
            <option value="">Select a member</option>
            {visible.map((member) => (
              <option key={member.id} value={member.id}>
                {label(member)} ({member.member_number})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Check-in code">
          <textarea
            autoComplete="off"
            className="input min-h-24"
            name="payload"
            onChange={(event) => setPayload(event.target.value)}
            required
            spellCheck={false}
            value={payload}
          />
        </Field>
        <button
          className="button"
          disabled={pending || !profileId}
          type="submit"
        >
          Scan check-in code
        </button>
      </form>
    </section>
  );
}

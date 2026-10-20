"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  OperationError,
  checkOutCheckIn,
  createCheckIn,
  listCheckIns,
  listMemberCheckIns,
  listMembers,
} from "@/features/operations/client";
import { TrainerScan } from "@/features/operations/trainer-scan";
import { formatInstant, zonedLocalToIso } from "@/lib/date";
import type { CheckInRecord, Member } from "@/types/api";

type DeskMember = Pick<
  Member,
  "id" | "member_number" | "first_name" | "last_name"
>;

function memberLabel(member: DeskMember): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return name ? `${name} (${member.member_number})` : member.member_number;
}

function failureMessage(caught: unknown): string {
  if (caught instanceof OperationError) return caught.message;
  if (caught instanceof Error && caught.message) return caught.message;
  return "The request could not be completed.";
}

function visitStatus(status: string): string {
  if (status === "OPEN") return "Open";
  if (status === "CHECKED_OUT") return "Checked out";
  return status;
}

function VisitList({
  visits,
  timeZone,
  empty,
}: {
  visits: CheckInRecord[];
  timeZone: string;
  empty: string;
}) {
  if (visits.length === 0) {
    return <p className="muted mt-3">{empty}</p>;
  }
  return (
    <ul className="mt-3 grid gap-3">
      {visits.map((visit) => (
        <li key={visit.id}>
          <p>
            {memberLabel(visit.member)} · {visitStatus(visit.status)}
          </p>
          <p className="muted">
            In {formatInstant(visit.check_in_at, timeZone)}
            {visit.check_out_at
              ? ` · Out ${formatInstant(visit.check_out_at, timeZone)}`
              : ""}
          </p>
          {visit.eligibility.plan_name ? (
            <p className="muted">
              {visit.eligibility.plan_name}
              {visit.eligibility.backdated ? " · Backdated" : ""}
            </p>
          ) : null}
          {visit.note ? <p>{visit.note}</p> : null}
        </li>
      ))}
    </ul>
  );
}

export function CheckInsManager({
  canCreate,
  canUpdate,
  canScan = false,
  membershipId = "",
  timeZone,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canScan?: boolean;
  membershipId?: string;
  timeZone: string;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<DeskMember[]>([]);
  const [selected, setSelected] = useState<DeskMember | null>(null);
  const [history, setHistory] = useState<CheckInRecord[]>([]);
  const [openVisits, setOpenVisits] = useState<CheckInRecord[]>([]);
  const [note, setNote] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [checkoutAt, setCheckoutAt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [loadingOpen, setLoadingOpen] = useState(true);
  const idempotency = useRef<{ fingerprint: string; key: string } | null>(null);

  async function refreshOpenVisits(): Promise<void> {
    const page = await listCheckIns({ open: "true", limit: 25 });
    setOpenVisits(page.data);
  }

  async function refreshHistory(memberId: string): Promise<void> {
    const page = await listMemberCheckIns(memberId);
    setHistory(page.data);
  }

  useEffect(() => {
    let cancelled = false;
    void listCheckIns({ open: "true", limit: 25 })
      .then((page) => {
        if (!cancelled) setOpenVisits(page.data);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(failureMessage(caught));
      })
      .finally(() => {
        if (!cancelled) setLoadingOpen(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function searchMembers(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const page = await listMembers({
        search,
        status: "ACTIVE",
        limit: 25,
      });
      setResults(page.data);
      if (page.data.length === 0) setNotice("No active members match.");
    } catch (caught) {
      setResults([]);
      setError(failureMessage(caught));
    } finally {
      setPending(false);
    }
  }

  async function selectMember(member: DeskMember) {
    setSelected(member);
    setNote("");
    setOccurredAt("");
    setCheckoutAt("");
    setError(null);
    setNotice(null);
    setPending(true);
    try {
      await refreshHistory(member.id);
    } catch (caught) {
      setHistory([]);
      setError(failureMessage(caught));
    } finally {
      setPending(false);
    }
  }

  async function submitCheckIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !canCreate) return;
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const occurred = occurredAt
        ? zonedLocalToIso(occurredAt, timeZone)
        : undefined;
      const fingerprint = `${selected.id}|${note.trim()}|${occurred ?? ""}`;
      if (
        !idempotency.current ||
        idempotency.current.fingerprint !== fingerprint
      ) {
        idempotency.current = {
          fingerprint,
          key: globalThis.crypto.randomUUID(),
        };
      }
      await createCheckIn({
        memberId: selected.id,
        note,
        idempotencyKey: idempotency.current.key,
        ...(occurred ? { occurredAt: occurred } : {}),
      });
      idempotency.current = null;
      setNote("");
      setOccurredAt("");
      setNotice("Checked in.");
      await Promise.all([refreshHistory(selected.id), refreshOpenVisits()]);
    } catch (caught) {
      setError(failureMessage(caught));
    } finally {
      setPending(false);
    }
  }

  async function submitCheckout(checkInId: string, localTime: string) {
    if (!canUpdate) return;
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const occurred = localTime
        ? zonedLocalToIso(localTime, timeZone)
        : undefined;
      await checkOutCheckIn(checkInId, occurred);
      setCheckoutAt("");
      setNotice("Checked out.");
      await refreshOpenVisits();
      if (selected) await refreshHistory(selected.id);
    } catch (caught) {
      setError(failureMessage(caught));
    } finally {
      setPending(false);
    }
  }

  const openForSelected = selected
    ? history.find((visit) => visit.status === "OPEN")
    : undefined;

  return (
    <div className="grid gap-4">
      {canScan ? <TrainerScan membershipId={membershipId} /> : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p aria-live="polite" className="muted">
          {notice}
        </p>
      ) : null}
      <section aria-labelledby="open-visits-title" className="card p-5">
        <h2 className="font-bold" id="open-visits-title">
          Currently checked in
        </h2>
        {loadingOpen ? (
          <p className="muted mt-3">Loading current visits.</p>
        ) : (
          <VisitList
            empty="Nobody is checked in."
            timeZone={timeZone}
            visits={openVisits}
          />
        )}
        {canUpdate
          ? openVisits.map((visit) => (
              <button
                key={visit.id}
                className="button mt-3"
                disabled={pending}
                onClick={() => void submitCheckout(visit.id, "")}
                type="button"
              >
                Check out {memberLabel(visit.member)}
              </button>
            ))
          : null}
      </section>
      <form className="card grid gap-4 p-5" onSubmit={searchMembers}>
        <label className="grid gap-1">
          Search active members
          <input
            className="input"
            name="search"
            onChange={(event) => setSearch(event.target.value)}
            required
            value={search}
          />
        </label>
        <button className="button" disabled={pending} type="submit">
          Search
        </button>
        {results.length > 0 ? (
          <ul className="grid gap-2">
            {results.map((member) => (
              <li key={member.id}>
                <button
                  className="button"
                  disabled={pending}
                  onClick={() => void selectMember(member)}
                  type="button"
                >
                  Select {memberLabel(member)}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </form>
      {selected ? (
        <section aria-labelledby="selected-member-title" className="card p-5">
          <h2 className="font-bold" id="selected-member-title">
            {memberLabel(selected)}
          </h2>
          <h3 className="mt-4 font-bold">Recent visits</h3>
          <VisitList
            empty="No check-ins yet."
            timeZone={timeZone}
            visits={history}
          />
          {canUpdate && openForSelected ? (
            <form
              className="mt-4 grid gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void submitCheckout(openForSelected.id, checkoutAt);
              }}
            >
              <label className="grid gap-1">
                Check-out time
                <input
                  className="input"
                  onChange={(event) => setCheckoutAt(event.target.value)}
                  type="datetime-local"
                  value={checkoutAt}
                />
              </label>
              <p className="muted">
                Leave blank to check out at the current time. A time is read in{" "}
                {timeZone}.
              </p>
              <button className="button" disabled={pending} type="submit">
                Check out
              </button>
            </form>
          ) : null}
          {canCreate ? (
            <form className="mt-4 grid gap-3" onSubmit={submitCheckIn}>
              <label className="grid gap-1">
                Note
                <input
                  className="input"
                  maxLength={500}
                  onChange={(event) => setNote(event.target.value)}
                  value={note}
                />
              </label>
              {canUpdate ? (
                <label className="grid gap-1">
                  Check-in time
                  <input
                    className="input"
                    onChange={(event) => setOccurredAt(event.target.value)}
                    type="datetime-local"
                    value={occurredAt}
                  />
                </label>
              ) : null}
              {canUpdate ? (
                <p className="muted">
                  Leave blank to use the current time. A time is read in{" "}
                  {timeZone} and can be up to 24 hours earlier.
                </p>
              ) : null}
              <button className="button" disabled={pending} type="submit">
                Check in
              </button>
            </form>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

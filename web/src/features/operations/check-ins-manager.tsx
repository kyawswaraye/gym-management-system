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

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function memberInitials(member: DeskMember): string {
  const letters = `${member.first_name} ${member.last_name}`
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
  return letters || member.member_number.slice(0, 2).toUpperCase();
}

function VisitList({
  visits,
  timeZone,
  empty,
  onCheckOut,
  pending = false,
}: {
  visits: CheckInRecord[];
  timeZone: string;
  empty: string;
  onCheckOut?: ((visit: CheckInRecord) => void) | undefined;
  pending?: boolean;
}) {
  if (visits.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center">
        <p className="text-sm font-medium text-slate-600">{empty}</p>
      </div>
    );
  }
  return (
    <ul className="grid gap-3">
      {visits.map((visit) => {
        const open = visit.status === "OPEN";
        return (
          <li
            className={
              open
                ? "rounded-xl border border-emerald-200 bg-emerald-50/70 p-4"
                : "rounded-xl border border-slate-200 bg-slate-50 p-4"
            }
            key={visit.id}
          >
            <span className="sr-only">
              {`${memberLabel(visit.member)} · ${visitStatus(visit.status)}`}
            </span>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p
                  aria-hidden="true"
                  className="font-semibold break-words text-slate-900"
                >
                  {memberLabel(visit.member)}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  In {formatInstant(visit.check_in_at, timeZone)}
                  {visit.check_out_at
                    ? ` · Out ${formatInstant(visit.check_out_at, timeZone)}`
                    : ""}
                </p>
              </div>
              <span
                aria-hidden="true"
                className={
                  open
                    ? "shrink-0 rounded-full bg-emerald-600 px-2.5 py-1 text-xs font-bold text-white"
                    : "shrink-0 rounded-full bg-white px-2.5 py-1 text-xs font-bold text-slate-600 ring-1 ring-inset ring-slate-200"
                }
              >
                {visitStatus(visit.status)}
              </span>
            </div>
            {visit.eligibility.plan_name ? (
              <p className="mt-3 text-sm text-slate-600">
                {visit.eligibility.plan_name}
                {visit.eligibility.backdated ? " · Backdated" : ""}
              </p>
            ) : null}
            {visit.note ? (
              <p className="mt-2 text-sm break-words text-slate-700">
                {visit.note}
              </p>
            ) : null}
            {onCheckOut && open ? (
              <button
                className="button mt-4 w-full sm:w-fit"
                disabled={pending}
                onClick={() => onCheckOut(visit)}
                type="button"
              >
                {`Check out ${memberLabel(visit.member)}`}
              </button>
            ) : null}
          </li>
        );
      })}
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

  const showCheckout = Boolean(canUpdate && openForSelected);
  const showCheckIn = canCreate;
  const searchMiss = notice === "No active members match.";

  return (
    <div className="grid gap-6">
      {canScan ? <TrainerScan membershipId={membershipId} /> : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {notice && !searchMiss ? (
        <p
          aria-live="polite"
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
          role="status"
        >
          {notice}
        </p>
      ) : null}
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <section aria-labelledby="open-visits-title" className={cardClass}>
          <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
                Live floor
              </p>
              <h2
                className="mt-1 text-xl font-bold text-slate-900"
                id="open-visits-title"
              >
                Currently checked in
              </h2>
            </div>
            {loadingOpen ? null : (
              <p
                aria-label={`${openVisits.length} Open`}
                className={`grid min-w-[4.5rem] shrink-0 place-items-center rounded-2xl px-3 py-2 text-center ${
                  openVisits.length > 0
                    ? "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-600/15"
                    : "bg-slate-100 text-slate-600"
                }`}
                role="status"
              >
                <span className="text-2xl font-black leading-none tabular-nums">
                  {openVisits.length}
                </span>
                <span className="mt-1 text-xs font-bold tracking-wide">
                  Open
                </span>
              </p>
            )}
          </div>
          <div className="p-5">
            {loadingOpen ? (
              <div
                className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center"
                role="status"
              >
                <p className="text-sm font-medium text-slate-600">
                  Loading current visits.
                </p>
              </div>
            ) : (
              <VisitList
                empty="Nobody is checked in."
                onCheckOut={
                  canUpdate
                    ? (visit) => void submitCheckout(visit.id, "")
                    : undefined
                }
                pending={pending}
                timeZone={timeZone}
                visits={openVisits}
              />
            )}
          </div>
        </section>
        <form className={cardClass} onSubmit={searchMembers}>
          <div className="border-b border-slate-100 px-5 py-4">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
              Member lookup
            </p>
            <h2 className="mt-1 text-xl font-bold text-slate-900">
              Find a member
            </h2>
          </div>
          <div className="grid gap-4 p-5">
            <label className="grid gap-2 text-sm font-semibold text-slate-800">
              Search active members
              <input
                className="input"
                name="search"
                onChange={(event) => setSearch(event.target.value)}
                required
                value={search}
              />
            </label>
            <button
              className="button w-full sm:w-fit"
              disabled={pending}
              type="submit"
            >
              Search
            </button>
            {results.length > 0 ? (
              <ul
                aria-label="Matching members"
                className="grid max-h-80 gap-2 overflow-y-auto p-1"
              >
                {results.map((member) => {
                  const active = selected?.id === member.id;
                  return (
                    <li key={member.id}>
                      <button
                        aria-pressed={active}
                        className={`button button-cancel w-full justify-start text-left ${
                          active
                            ? "outline outline-2 outline-offset-2 outline-amber-400"
                            : ""
                        }`}
                        disabled={pending}
                        onClick={() => void selectMember(member)}
                        type="button"
                      >
                        {`Select ${memberLabel(member)}`}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {searchMiss ? (
              <p
                aria-live="polite"
                className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-sm font-medium text-slate-600"
                role="status"
              >
                {notice}
              </p>
            ) : null}
          </div>
        </form>
      </div>
      {selected ? (
        <section aria-labelledby="selected-member-title" className={cardClass}>
          <div className="flex items-center gap-4 border-b border-slate-100 px-5 py-4">
            <span
              aria-hidden="true"
              className="grid size-12 shrink-0 place-items-center rounded-2xl bg-amber-100 text-sm font-black text-amber-900"
            >
              {memberInitials(selected)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
                Selected member
              </p>
              <h2
                className="mt-1 text-xl font-bold break-words text-slate-900"
                id="selected-member-title"
              >
                {memberLabel(selected)}
              </h2>
            </div>
            {openForSelected ? (
              <span
                aria-hidden="true"
                className="shrink-0 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-bold text-emerald-800 ring-1 ring-inset ring-emerald-600/20"
              >
                Open
              </span>
            ) : null}
          </div>
          <div className="grid gap-5 p-5">
            <div>
              <h3 className="text-sm font-bold text-slate-900">
                Recent visits
              </h3>
              <div className="mt-3">
                <VisitList
                  empty="No check-ins yet."
                  timeZone={timeZone}
                  visits={history}
                />
              </div>
            </div>
            {showCheckout || showCheckIn ? (
              <div
                className={`grid gap-4 border-t border-slate-100 pt-5 ${
                  showCheckout && showCheckIn ? "lg:grid-cols-2" : ""
                }`}
              >
                {showCheckout && openForSelected ? (
                  <form
                    className="grid content-start gap-3 rounded-xl bg-slate-50 p-4"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitCheckout(openForSelected.id, checkoutAt);
                    }}
                  >
                    <label className="grid gap-2 text-sm font-semibold text-slate-800">
                      Check-out time
                      <input
                        className="input"
                        onChange={(event) => setCheckoutAt(event.target.value)}
                        type="datetime-local"
                        value={checkoutAt}
                      />
                    </label>
                    <p className="text-sm leading-5 text-slate-500">
                      Leave blank to check out at the current time. A time is
                      read in {timeZone}.
                    </p>
                    <button
                      className="button w-full sm:w-fit"
                      disabled={pending}
                      type="submit"
                    >
                      Check out
                    </button>
                  </form>
                ) : null}
                {showCheckIn ? (
                  <form
                    className="grid content-start gap-3 rounded-xl bg-slate-50 p-4"
                    onSubmit={submitCheckIn}
                  >
                    <label className="grid gap-2 text-sm font-semibold text-slate-800">
                      Note
                      <input
                        className="input"
                        maxLength={500}
                        onChange={(event) => setNote(event.target.value)}
                        value={note}
                      />
                    </label>
                    {canUpdate ? (
                      <label className="grid gap-2 text-sm font-semibold text-slate-800">
                        Check-in time
                        <input
                          className="input"
                          onChange={(event) =>
                            setOccurredAt(event.target.value)
                          }
                          type="datetime-local"
                          value={occurredAt}
                        />
                      </label>
                    ) : null}
                    {canUpdate ? (
                      <p className="text-sm leading-5 text-slate-500">
                        Leave blank to use the current time. A time is read in{" "}
                        {timeZone} and can be up to 24 hours earlier.
                      </p>
                    ) : null}
                    <button
                      className="button button-create w-full sm:w-fit"
                      disabled={pending}
                      type="submit"
                    >
                      Check in
                    </button>
                  </form>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

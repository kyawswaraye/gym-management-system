"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { listMembers, operationMutation } from "@/features/operations/client";
import type { Member, MemberListPage, MemberListQuery } from "@/types/api";

const PAGE_LIMIT = 25;
const MEMBER_STATUSES = [
  "ACTIVE",
  "EXPIRED",
  "SUSPENDED",
  "CANCELLED",
  "ARCHIVED",
] as const satisfies readonly NonNullable<MemberListQuery["status"]>[];

type MemberStatus = (typeof MEMBER_STATUSES)[number];

type PtClientFilter = "" | "true";

type MemberFilters = {
  search: string;
  status: "" | MemberStatus;
  joinedFrom: string;
  joinedTo: string;
  ptClient: PtClientFilter;
};

function isMemberStatus(value: string): value is MemberStatus {
  return (MEMBER_STATUSES as readonly string[]).includes(value);
}

function memberQuery(filters: MemberFilters, after?: string): MemberListQuery {
  const query: MemberListQuery = { limit: PAGE_LIMIT };
  const search = filters.search.trim();
  if (search) query.search = search;
  if (filters.status) query.status = filters.status;
  if (filters.joinedFrom) query.joined_from = filters.joinedFrom;
  if (filters.joinedTo) query.joined_to = filters.joinedTo;
  if (filters.ptClient === "true") query.is_pt_client = true;
  if (after) query.after = after;
  return query;
}

function uniqueMembers(rows: Member[]): Member[] {
  const seen = new Set<string>();
  return rows.filter((member) => {
    if (seen.has(member.id)) return false;
    seen.add(member.id);
    return true;
  });
}

function appendMembers(current: Member[], incoming: Member[]): Member[] {
  const seen = new Set(current.map((member) => member.id));
  const next = incoming.filter((member) => {
    if (seen.has(member.id)) return false;
    seen.add(member.id);
    return true;
  });
  return next.length === 0 ? current : [...current, ...next];
}

function errorText(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function applyCursor(
  meta: MemberListPage["meta"],
  cursor: string | undefined,
  applied: Set<string>,
): { hasMore: boolean; nextAfter: string | null } {
  const next = meta.next_after;
  const stop = !meta.has_more || !next || next === cursor || applied.has(next);
  return { hasMore: !stop, nextAfter: stop ? null : next };
}

export function MembersManager({
  canCreate,
  defaultDate,
}: {
  canCreate: boolean;
  defaultDate: string;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState("");
  const [errorScope, setErrorScope] = useState<"list" | "page" | null>(null);
  const [pending, setPending] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | MemberStatus>("");
  const [joinedFrom, setJoinedFrom] = useState("");
  const [joinedTo, setJoinedTo] = useState("");
  const [ptClient, setPtClient] = useState<PtClientFilter>("");
  const requestVersion = useRef(0);
  const appliedCursors = useRef(new Set<string>());
  const filters = { search, status, joinedFrom, joinedTo, ptClient };

  function resetPage() {
    appliedCursors.current = new Set();
    requestVersion.current += 1;
    setMembers([]);
    setNextAfter(null);
    setHasMore(false);
    setLoading(true);
    setPaging(false);
    setError("");
    setErrorScope(null);
  }

  async function loadFirst() {
    const version = ++requestVersion.current;
    appliedCursors.current = new Set();
    setMembers([]);
    setNextAfter(null);
    setHasMore(false);
    setLoading(true);
    setPaging(false);
    setError("");
    setErrorScope(null);
    try {
      const page = await listMembers(memberQuery(filters));
      if (version !== requestVersion.current) return;
      setMembers(uniqueMembers(page.data));
      const cursor = applyCursor(page.meta, undefined, appliedCursors.current);
      setHasMore(cursor.hasMore);
      setNextAfter(cursor.nextAfter);
    } catch (caught) {
      if (version !== requestVersion.current) return;
      setMembers([]);
      setNextAfter(null);
      setHasMore(false);
      setError(errorText(caught, "Unable to load members."));
      setErrorScope("list");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  useEffect(() => {
    const version = ++requestVersion.current;
    const currentFilters = { search, status, joinedFrom, joinedTo, ptClient };
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const page = await listMembers(memberQuery(currentFilters));
          if (version !== requestVersion.current) return;
          setMembers(uniqueMembers(page.data));
          const cursor = applyCursor(
            page.meta,
            undefined,
            appliedCursors.current,
          );
          setHasMore(cursor.hasMore);
          setNextAfter(cursor.nextAfter);
          setError("");
          setErrorScope(null);
        } catch (caught) {
          if (version !== requestVersion.current) return;
          setMembers([]);
          setNextAfter(null);
          setHasMore(false);
          setError(errorText(caught, "Unable to load members."));
          setErrorScope("list");
        } finally {
          if (version === requestVersion.current) setLoading(false);
        }
      })();
    }, 250);
    return () => {
      window.clearTimeout(timer);
      requestVersion.current += 1;
    };
  }, [joinedFrom, joinedTo, ptClient, search, status]);

  async function loadMore() {
    const cursor = nextAfter;
    if (
      !hasMore ||
      !cursor ||
      paging ||
      loading ||
      appliedCursors.current.has(cursor)
    ) {
      if (cursor && appliedCursors.current.has(cursor)) {
        setHasMore(false);
        setNextAfter(null);
      }
      return;
    }
    const version = ++requestVersion.current;
    appliedCursors.current.add(cursor);
    setPaging(true);
    setError("");
    setErrorScope(null);
    try {
      const page = await listMembers(memberQuery(filters, cursor));
      if (version !== requestVersion.current) return;
      setMembers((current) => appendMembers(current, page.data));
      const next = applyCursor(page.meta, cursor, appliedCursors.current);
      setHasMore(next.hasMore);
      setNextAfter(next.nextAfter);
    } catch (caught) {
      appliedCursors.current.delete(cursor);
      if (version === requestVersion.current) {
        setError(errorText(caught, "Unable to load members."));
        setErrorScope("page");
      }
    } finally {
      if (version === requestVersion.current) setPaging(false);
    }
  }

  function retry() {
    if (errorScope === "page") {
      void loadMore();
      return;
    }
    void loadFirst();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await operationMutation<Member>("members/", {
        first_name: form.get("first_name"),
        last_name: form.get("last_name"),
        phone: form.get("phone"),
        email: form.get("email"),
        join_date: form.get("join_date"),
      });
      formElement.reset();
      await loadFirst();
    } catch (caught) {
      setError(errorText(caught, "Unable to create member."));
      setErrorScope(null);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      {canCreate && (
        <form className="card grid gap-4 p-5" onSubmit={submit}>
          <h2 className="text-lg font-bold">Add member</h2>
          <label className="field">
            First name
            <input className="input" name="first_name" required />
          </label>
          <label className="field">
            Last name
            <input className="input" name="last_name" />
          </label>
          <label className="field">
            Phone
            <input className="input" name="phone" />
          </label>
          <label className="field">
            Email
            <input className="input" name="email" type="email" />
          </label>
          <label className="field">
            Join date
            <input
              className="input"
              defaultValue={defaultDate}
              name="join_date"
              required
              type="date"
            />
          </label>
          <button className="button" disabled={pending} type="submit">
            {pending ? "Saving…" : "Save member"}
          </button>
        </form>
      )}
      <section aria-busy={loading || paging} className="card overflow-hidden">
        <div className="flex flex-wrap gap-3 border-b border-slate-200 p-4">
          <input
            aria-label="Search members"
            className="input max-w-sm"
            maxLength={100}
            onChange={(event) => {
              setSearch(event.target.value);
              resetPage();
            }}
            placeholder="Search name, number, phone, email"
            value={search}
          />
          <select
            aria-label="Filter by status"
            className="input max-w-48"
            onChange={(event) => {
              const value = event.target.value;
              setStatus(isMemberStatus(value) ? value : "");
              resetPage();
            }}
            value={status}
          >
            <option value="">All statuses</option>
            <option value="ACTIVE">Active</option>
            <option value="EXPIRED">Expired</option>
            <option value="SUSPENDED">Suspended</option>
            <option value="CANCELLED">Cancelled</option>
            <option value="ARCHIVED">Archived</option>
          </select>
          <select
            aria-label="Filter by PT client"
            className="input max-w-56"
            onChange={(event) => {
              setPtClient(event.target.value === "true" ? "true" : "");
              resetPage();
            }}
            value={ptClient}
          >
            <option value="">All members</option>
            <option value="true">Active PT clients</option>
          </select>
          <input
            aria-label="Joined from"
            className="input max-w-44"
            max={joinedTo || undefined}
            onChange={(event) => {
              const value = event.target.value;
              setJoinedFrom(value);
              if (joinedTo && value > joinedTo) setJoinedTo("");
              resetPage();
            }}
            type="date"
            value={joinedFrom}
          />
          <input
            aria-label="Joined to"
            className="input max-w-44"
            min={joinedFrom || undefined}
            onChange={(event) => {
              setJoinedTo(event.target.value);
              resetPage();
            }}
            type="date"
            value={joinedTo}
          />
        </div>
        {loading ? (
          <p className="muted m-4" role="status">
            Loading members.
          </p>
        ) : null}
        {error ? (
          <div className="m-4 grid gap-3">
            <p className="error" role="alert">
              {error}
            </p>
            {errorScope ? (
              <button className="button" onClick={retry} type="button">
                {errorScope === "page" ? "Retry loading members" : "Retry"}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3" scope="col">
                  Member
                </th>
                <th className="p-3" scope="col">
                  Phone
                </th>
                <th className="p-3" scope="col">
                  Joined
                </th>
                <th className="p-3" scope="col">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr className="border-t border-slate-100" key={member.id}>
                  <td className="p-3">
                    <Link
                      className="font-semibold underline"
                      href={`/members/${member.id}`}
                    >
                      {`${member.first_name} ${member.last_name}`.trim()}
                      {member.is_pt_client && (
                        <span className="ml-2 rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
                          PT
                        </span>
                      )}
                    </Link>
                    <span className="muted block">{member.member_number}</span>
                  </td>
                  <td className="p-3">{member.phone || "—"}</td>
                  <td className="p-3">{member.join_date}</td>
                  <td className="p-3">{member.status}</td>
                </tr>
              ))}
              {!loading && !error && members.length === 0 ? (
                <tr>
                  <td className="muted p-6 text-center" colSpan={4}>
                    No members found.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {paging ? (
          <p className="muted m-4" role="status">
            Loading more members.
          </p>
        ) : null}
        {hasMore && nextAfter ? (
          <div className="p-4">
            <button
              className="button"
              disabled={paging}
              onClick={() => void loadMore()}
              type="button"
            >
              Load more members
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}

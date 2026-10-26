"use client";

import { Fragment, type FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";

import {
  getGymSettings,
  listMembers,
  operationMutation,
} from "@/features/operations/client";
import {
  MemberProfileFields,
  type MemberNumberPreview,
} from "@/features/operations/member-profile-fields";
import {
  assignedMemberNumber,
  emptyMemberProfile,
  memberCreatePayload,
  type MemberProfileInput,
} from "@/features/operations/member-profile";
import {
  ArchiveMemberDialog,
  memberArchiveIdentity,
  memberDisplayName,
  RestoreMemberDialog,
} from "@/features/operations/member-restore-dialog";
import { actionButtonClass } from "@/lib/ui/action-button";
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

const DIRECTORY_VIEWS: ReadonlyArray<{
  value: "" | MemberStatus;
  label: string;
}> = [
  { value: "", label: "All" },
  { value: "ACTIVE", label: "Active" },
  { value: "EXPIRED", label: "Expired" },
  { value: "SUSPENDED", label: "Suspended" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "ARCHIVED", label: "Archived" },
];

type PtClientFilter = "" | "true";

type MemberFilters = {
  search: string;
  status: "" | MemberStatus;
  joinedFrom: string;
  joinedTo: string;
  ptClient: PtClientFilter;
};

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

function directoryButtonClass(
  value: "" | MemberStatus,
  selected: boolean,
): string {
  const base =
    "min-h-10 rounded-full px-3.5 py-1.5 text-sm font-bold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900";
  if (value === "ARCHIVED") {
    return selected
      ? `${base} border border-slate-900 bg-slate-700 text-white`
      : `${base} border border-slate-500 bg-slate-100 text-slate-800 hover:bg-slate-200`;
  }
  return selected
    ? `${base} border border-slate-900 bg-slate-900 text-white`
    : `${base} border border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200`;
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

const UNAVAILABLE_MEMBER_NUMBER: MemberNumberPreview = {
  status: "unavailable",
  value: "",
};

export function MembersManager({
  canArchive = false,
  canCreate,
  canPreviewMemberNumber = false,
  canRestore = false,
  defaultDate,
}: {
  canArchive?: boolean;
  canCreate: boolean;
  canPreviewMemberNumber?: boolean;
  canRestore?: boolean;
  defaultDate: string;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState("");
  const [errorScope, setErrorScope] = useState<
    "list" | "page" | "action" | null
  >(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [archiveTarget, setArchiveTarget] = useState<Member | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<Member | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [memberNumberPreview, setMemberNumberPreview] =
    useState<MemberNumberPreview>(UNAVAILABLE_MEMBER_NUMBER);
  const createDialogRef = useRef<HTMLFormElement>(null);
  const [draft, setDraft] = useState<MemberProfileInput>(() =>
    emptyMemberProfile(defaultDate),
  );
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | MemberStatus>("");
  const [joinedFrom, setJoinedFrom] = useState("");
  const [joinedTo, setJoinedTo] = useState("");
  const [ptClient, setPtClient] = useState<PtClientFilter>("");
  const requestVersion = useRef(0);
  const appliedCursors = useRef(new Set<string>());
  const submitLock = useRef(false);
  const archiveLock = useRef(false);
  const restoreLock = useRef(false);
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
    setNotice("");
    setArchiveTarget(null);
    setRestoreTarget(null);
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
    setNotice("");
    setArchiveTarget(null);
    setRestoreTarget(null);
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

  useEffect(() => {
    if (!showCreate) return;
    createDialogRef.current?.focus({ preventScroll: true });
  }, [showCreate]);

  useEffect(() => {
    if (!showCreate) return;
    if (!canPreviewMemberNumber) {
      setMemberNumberPreview(UNAVAILABLE_MEMBER_NUMBER);
      return;
    }
    let cancelled = false;
    setMemberNumberPreview({ status: "loading", value: "" });
    void getGymSettings()
      .then((settings) => {
        if (cancelled) return;
        setMemberNumberPreview({
          status: "ready",
          value: assignedMemberNumber(
            settings.member_number_prefix,
            settings.next_member_number,
          ),
        });
      })
      .catch(() => {
        if (cancelled) return;
        setMemberNumberPreview(UNAVAILABLE_MEMBER_NUMBER);
      });
    return () => {
      cancelled = true;
    };
  }, [canPreviewMemberNumber, showCreate]);

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

  function selectStatus(next: "" | MemberStatus) {
    if (next === status) return;
    setStatus(next);
    resetPage();
  }

  async function confirmArchive() {
    const target = archiveTarget;
    if (
      !target ||
      archiveLock.current ||
      archiving ||
      restoring ||
      !canArchive ||
      target.status === "ARCHIVED"
    ) {
      return;
    }
    archiveLock.current = true;
    const version = requestVersion.current;
    setArchiveTarget(null);
    setArchiving(true);
    setError("");
    setErrorScope(null);
    try {
      const archived = await operationMutation<Member>(
        `members/${target.id}/archive/`,
        {},
      );
      if (version !== requestVersion.current) return;
      setMembers((current) =>
        status === "" || status === "ARCHIVED"
          ? current.map((row) => (row.id === archived.id ? archived : row))
          : current.filter((row) => row.id !== target.id),
      );
      setNotice(`${memberDisplayName(target)} archived.`);
    } catch (caught) {
      if (version !== requestVersion.current) return;
      setError(errorText(caught, "Unable to archive member."));
      setErrorScope("action");
    } finally {
      archiveLock.current = false;
      setArchiving(false);
    }
  }

  async function confirmRestore() {
    const target = restoreTarget;
    if (
      !target ||
      restoreLock.current ||
      restoring ||
      archiving ||
      !canRestore ||
      target.status !== "ARCHIVED"
    ) {
      return;
    }
    restoreLock.current = true;
    const version = requestVersion.current;
    setRestoreTarget(null);
    setRestoring(true);
    setError("");
    setErrorScope(null);
    try {
      const restored = await operationMutation<Member>(
        `members/${target.id}/restore/`,
        {},
      );
      if (version !== requestVersion.current) return;
      setMembers((current) =>
        status === "" || status === "ACTIVE"
          ? current.map((row) => (row.id === restored.id ? restored : row))
          : current.filter((row) => row.id !== target.id),
      );
      setNotice(`${memberDisplayName(target)} restored to Active.`);
    } catch (caught) {
      if (version !== requestVersion.current) return;
      setError(errorText(caught, "Unable to restore member."));
      setErrorScope("action");
    } finally {
      restoreLock.current = false;
      setRestoring(false);
    }
  }

  function closeCreate() {
    if (pending || submitLock.current) return;
    setShowCreate(false);
    setDraft(emptyMemberProfile(defaultDate));
    if (!errorScope) setError("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitLock.current) return;
    const payload = memberCreatePayload({ ...draft, member_number: "" });
    if (!payload.first_name || !payload.last_name || !payload.join_date) {
      setError("Enter a first name, last name, and join date.");
      setErrorScope(null);
      return;
    }
    submitLock.current = true;
    setPending(true);
    try {
      await operationMutation<Member>("members/", payload);
      setDraft(emptyMemberProfile(defaultDate));
      setShowCreate(false);
      await loadFirst();
    } catch (caught) {
      setError(errorText(caught, "Unable to create member."));
      setErrorScope(null);
    } finally {
      submitLock.current = false;
      setPending(false);
    }
  }

  const showingArchived = status === "ARCHIVED";
  const archivedDirectoryEmpty =
    showingArchived && !search && !joinedFrom && !joinedTo && !ptClient;
  const showActions = canArchive || canRestore;
  const tableColumns = showActions ? 5 : 4;
  const commandPending = archiving || restoring;

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-700">
            {loading
              ? showingArchived
                ? "Loading archived members…"
                : "Loading directory…"
              : showingArchived
                ? `${members.length} archived members shown`
                : `${members.length} members shown`}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {showingArchived
              ? "Open a member for the full profile."
              : "Select a member to view their full profile."}
          </p>
        </div>
        {canCreate && (
          <button
            aria-expanded={showCreate}
            className="button button-create shrink-0"
            onClick={() => setShowCreate(true)}
            type="button"
          >
            Add member
          </button>
        )}
      </div>
      {canCreate && showCreate ? (
        <div
          className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
          onMouseDown={(event) => {
            if (event.target !== event.currentTarget) return;
            closeCreate();
          }}
          role="presentation"
        >
          <form
            aria-describedby="add-member-description"
            aria-labelledby="add-member-title"
            aria-modal="true"
            className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,52rem)] sm:max-w-3xl"
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              closeCreate();
            }}
            onSubmit={submit}
            ref={createDialogRef}
            role="dialog"
            tabIndex={-1}
          >
            <div className="sticky top-0 z-10 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <h2
                className="text-xl font-bold text-slate-900"
                id="add-member-title"
              >
                Add member
              </h2>
              <p
                className="mt-1 text-sm text-slate-500"
                id="add-member-description"
              >
                Enter the member&apos;s personal and contact information.
              </p>
            </div>
            <div className="grid gap-4 px-5 py-4 sm:grid-cols-2 sm:px-6 [&>fieldset]:sm:col-span-2 [&>label:last-child]:sm:col-span-2">
              <MemberProfileFields
                disabled={pending}
                includeMemberNumber
                memberNumberPreview={memberNumberPreview}
                onChange={setDraft}
                value={draft}
              />
            </div>
            <div className="sticky bottom-0 z-10 mt-auto border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
              {error && !errorScope ? (
                <p
                  className="mb-3 text-sm font-medium text-red-800"
                  role="alert"
                >
                  {error}
                </p>
              ) : null}
              <div className="flex flex-wrap justify-end gap-2">
                <button
                  className="button button-cancel"
                  disabled={pending}
                  onClick={closeCreate}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button button-edit"
                  disabled={pending}
                  type="submit"
                >
                  {pending ? "Saving…" : "Save member"}
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}
      <section
        aria-busy={loading || paging}
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="border-b border-slate-200 px-4 py-4 sm:px-5">
          <h2 className="text-sm font-bold tracking-wide text-slate-700 uppercase">
            Directory view
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
            {showingArchived
              ? canRestore
                ? "These members are archived. Open a name for the full profile, or restore them to Active. Membership and payment history stay in place, and the record is not deleted."
                : "These members are archived. Open a name for the full profile."
              : "Choose who appears in this table. Archived members are listed in their own view."}
          </p>
          <div
            aria-label="Member status"
            className="mt-3 flex flex-wrap items-center gap-2"
            role="radiogroup"
          >
            {DIRECTORY_VIEWS.map((view) => (
              <Fragment key={view.value || "ALL"}>
                {view.value === "ARCHIVED" ? (
                  <span
                    aria-hidden="true"
                    className="mx-1 hidden h-6 w-px bg-slate-300 sm:block"
                  />
                ) : null}
                <button
                  aria-checked={status === view.value}
                  className={directoryButtonClass(
                    view.value,
                    status === view.value,
                  )}
                  onClick={() => selectStatus(view.value)}
                  role="radio"
                  type="button"
                >
                  {view.label}
                </button>
              </Fragment>
            ))}
          </div>
        </div>
        <div className="border-b border-slate-200 bg-slate-50/70 p-4 sm:p-5">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-bold tracking-wide text-slate-700 uppercase">
              Search and filters
            </h2>
            {(search || status || joinedFrom || joinedTo || ptClient) && (
              <button
                className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline"
                onClick={() => {
                  setSearch("");
                  setStatus("");
                  setJoinedFrom("");
                  setJoinedTo("");
                  setPtClient("");
                  resetPage();
                }}
                type="button"
              >
                Clear filters
              </button>
            )}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(16rem,1fr)_repeat(3,auto)]">
            <input
              aria-label="Search members"
              className="input w-full"
              maxLength={100}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
              placeholder="Search name, number, phone, email"
              value={search}
            />
            <select
              aria-label="Filter by PT client"
              className="input xl:w-44"
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
              className="input xl:w-40"
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
              className="input xl:w-40"
              min={joinedFrom || undefined}
              onChange={(event) => {
                setJoinedTo(event.target.value);
                resetPage();
              }}
              type="date"
              value={joinedTo}
            />
          </div>
        </div>
        {loading ? (
          <div className="grid gap-3 p-5" role="status">
            <span className="sr-only">Loading members.</span>
            {[0, 1, 2].map((item) => (
              <div
                className="h-16 animate-pulse rounded-xl bg-slate-100"
                key={item}
              />
            ))}
          </div>
        ) : null}
        {error && errorScope ? (
          <div className="m-4 grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
            <p className="text-sm font-medium text-red-800" role="alert">
              {error}
            </p>
            {errorScope === "list" || errorScope === "page" ? (
              <button className="button w-fit" onClick={retry} type="button">
                {errorScope === "page" ? "Retry loading members" : "Retry"}
              </button>
            ) : null}
          </div>
        ) : null}
        {notice ? (
          <p
            className="m-4 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm font-medium text-teal-900"
            role="status"
          >
            {notice}
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Member
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Phone
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Joined
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Status
                </th>
                {showActions ? (
                  <th className="px-5 py-3 text-xs font-semibold" scope="col">
                    Action
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((member) => {
                const archived = member.status === "ARCHIVED";
                const name = memberDisplayName(member);
                return (
                  <tr
                    className={
                      archived
                        ? "bg-slate-100 text-slate-600"
                        : "bg-white hover:bg-slate-50"
                    }
                    data-status={member.status}
                    key={member.id}
                  >
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <span
                          className={`grid size-10 shrink-0 place-items-center rounded-full text-xs font-black ${archived ? "bg-slate-200 text-slate-500" : "bg-slate-100 text-slate-600"}`}
                          aria-hidden="true"
                        >
                          {member.first_name.slice(0, 1)}
                          {member.last_name.slice(0, 1)}
                        </span>
                        <span>
                          <Link
                            className="font-bold"
                            href={`/members/${member.id}`}
                          >
                            {`${member.first_name} ${member.last_name}`.trim()}
                            {member.is_pt_client && (
                              <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                                PT
                              </span>
                            )}
                          </Link>
                          <span className="mt-0.5 block text-xs text-slate-500">
                            {member.member_number}
                          </span>
                        </span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                      {member.phone || "—"}
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                      {member.join_date}
                    </td>
                    <td className="px-5 py-4">
                      <MemberStatusBadge status={member.status} />
                    </td>
                    {showActions ? (
                      <td className="whitespace-nowrap px-5 py-4">
                        {canArchive && !archived ? (
                          <button
                            aria-label={`Archive ${name}`}
                            className={actionButtonClass(
                              "Archive member",
                              "button-sm",
                            )}
                            disabled={
                              commandPending ||
                              archiveTarget !== null ||
                              restoreTarget !== null
                            }
                            onClick={() => {
                              if (
                                archiveLock.current ||
                                commandPending ||
                                archiveTarget ||
                                restoreTarget
                              ) {
                                return;
                              }
                              setArchiveTarget(member);
                            }}
                            type="button"
                          >
                            Archive
                          </button>
                        ) : null}
                        {archived && canRestore ? (
                          <button
                            aria-label={`Restore ${name}`}
                            className={actionButtonClass(
                              "Restore member",
                              "button-sm",
                            )}
                            disabled={
                              commandPending ||
                              archiveTarget !== null ||
                              restoreTarget !== null
                            }
                            onClick={() => {
                              if (
                                restoreLock.current ||
                                commandPending ||
                                archiveTarget ||
                                restoreTarget
                              ) {
                                return;
                              }
                              setRestoreTarget(member);
                            }}
                            type="button"
                          >
                            Restore
                          </button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
              {!loading && !error && members.length === 0 ? (
                <tr>
                  <td className="px-6 py-14 text-center" colSpan={tableColumns}>
                    <p className="font-semibold text-slate-700">
                      {archivedDirectoryEmpty
                        ? "No archived members."
                        : "No members found."}
                    </p>
                    <p className="mt-1 text-sm text-slate-500">
                      {archivedDirectoryEmpty
                        ? "Members you archive will appear in this view."
                        : "Try changing or clearing the current filters."}
                    </p>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {paging ? (
          <p className="m-4 text-center text-sm text-slate-500" role="status">
            Loading more members.
          </p>
        ) : null}
        {hasMore && nextAfter ? (
          <div className="flex justify-center border-t border-slate-100 p-4">
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
      {archiveTarget ? (
        <ArchiveMemberDialog
          identity={memberArchiveIdentity(archiveTarget)}
          onCancel={() => setArchiveTarget(null)}
          onConfirm={() => void confirmArchive()}
        />
      ) : null}
      {restoreTarget ? (
        <RestoreMemberDialog
          identity={memberArchiveIdentity(restoreTarget)}
          onCancel={() => setRestoreTarget(null)}
          onConfirm={() => void confirmRestore()}
        />
      ) : null}
    </div>
  );
}

function MemberStatusBadge({ status }: { status: string }) {
  const tones: Record<string, string> = {
    ACTIVE: "bg-emerald-100 text-emerald-700",
    EXPIRED: "bg-slate-100 text-slate-600",
    SUSPENDED: "bg-amber-100 text-amber-800",
    CANCELLED: "bg-red-100 text-red-700",
    ARCHIVED: "bg-slate-200 text-slate-600",
  };
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${tones[status] ?? "bg-slate-100 text-slate-600"}`}
    >
      {status}
    </span>
  );
}

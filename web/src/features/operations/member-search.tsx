"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useRef, useState, type Ref } from "react";

import { OperationError, listMembers } from "@/features/operations/client";
import { Field } from "@/features/finance/ui";
import type { Member } from "@/types/api";

const SEARCH_LIMIT = 10;
const SEARCH_DEBOUNCE_MS = 250;

export type PickedMember = Pick<
  Member,
  "id" | "member_number" | "first_name" | "last_name"
>;

export function memberLabel(member: PickedMember): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return name ? `${name} (${member.member_number})` : member.member_number;
}

function searchErrorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof OperationError) return caught.message;
  if (caught instanceof Error && caught.message) return caught.message;
  return fallback;
}

export function MemberSearch({
  selected,
  onSelect,
  disabled,
  label = "Search active members",
  activeOnly = true,
  formatMember = memberLabel,
  showEmptySelected = false,
  emptySelectedLabel = "No member selected.",
  inputRef,
}: {
  selected: PickedMember | null;
  onSelect: (member: Member) => void;
  disabled?: boolean;
  label?: string;
  activeOnly?: boolean;
  formatMember?: (member: PickedMember) => string;
  showEmptySelected?: boolean;
  emptySelectedLabel?: string;
  inputRef?: Ref<HTMLInputElement>;
}) {
  const tx = useTx();
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Member[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const searchVersion = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);

  const trimmed = search.trim();
  const panelOpen = trimmed.length > 0;

  useEffect(() => {
    if (!trimmed) {
      setResults([]);
      setHasMore(false);
      setError("");
      setPending(false);
      return;
    }

    const version = ++searchVersion.current;
    setPending(true);
    setError("");
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const page = await listMembers({
            search: trimmed,
            ...(activeOnly ? { status: "ACTIVE" } : {}),
            limit: SEARCH_LIMIT,
          });
          if (version !== searchVersion.current) return;
          setResults(page.data);
          setHasMore(Boolean(page.meta.has_more));
          setError(
            page.data.length === 0
              ? tx(
                  activeOnly
                    ? "No active members match."
                    : "No matching members.",
                )
              : "",
          );
        } catch (caught) {
          if (version !== searchVersion.current) return;
          setResults([]);
          setHasMore(false);
          setError(searchErrorMessage(caught, tx("Unable to search members.")));
        } finally {
          if (version === searchVersion.current) setPending(false);
        }
      })();
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      searchVersion.current += 1;
    };
  }, [activeOnly, search, trimmed, tx]);

  useEffect(() => {
    if (!panelOpen) return undefined;

    function clearPanel() {
      setSearch("");
      setResults([]);
      setHasMore(false);
      setError("");
      setPending(false);
      searchVersion.current += 1;
    }

    function onPointerDown(event: MouseEvent) {
      const root = rootRef.current;
      if (!root || root.contains(event.target as Node)) return;
      clearPanel();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      clearPanel();
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [panelOpen]);

  function choose(member: Member) {
    onSelect(member);
    setSearch("");
    setResults([]);
    setHasMore(false);
    setError("");
    setPending(false);
    searchVersion.current += 1;
  }

  return (
    <div className="grid gap-2" ref={rootRef}>
      <div className="relative z-20">
        <Field label={label}>
          <input
            autoComplete="off"
            className="input"
            disabled={disabled}
            maxLength={100}
            name="member_search"
            onChange={(event) => setSearch(event.target.value)}
            placeholder={tx("Name, number, phone, or email")}
            ref={inputRef}
            value={search}
          />
        </Field>
        {panelOpen ? (
          <div className="absolute top-full right-0 left-0 z-30 mt-1 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
            {pending ? (
              <p className="px-3 py-2 text-sm text-slate-500" role="status">
                {tx("Searching members.")}
              </p>
            ) : null}
            {!pending && error ? (
              <p className="error px-3 py-2" role="alert">
                {tx(error)}
              </p>
            ) : null}
            {!pending && results.length > 0 ? (
              <ul
                aria-label={tx("Matching members")}
                className="grid max-h-40 gap-0.5 overflow-y-auto overscroll-contain p-1"
              >
                {results.map((member) => (
                  <li key={member.id}>
                    <button
                      aria-pressed={selected?.id === member.id}
                      className="w-full rounded-md px-2 py-1.5 text-left text-sm font-semibold text-slate-700 hover:bg-slate-100 aria-pressed:bg-amber-100 aria-pressed:text-amber-900"
                      disabled={disabled}
                      onClick={() => choose(member)}
                      type="button"
                    >
                      {formatMember(member)}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {!pending && hasMore ? (
              <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
                {tx("Showing the first matches. Type more to narrow the list.")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
      {selected ? (
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800">
          {tx("Selected member: {name}", { name: formatMember(selected) })}
        </p>
      ) : showEmptySelected ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-sm text-slate-500">
          {tx(emptySelectedLabel)}
        </p>
      ) : null}
    </div>
  );
}

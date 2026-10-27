"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  buildAuditListPath,
  formatAuditValue,
  type AuditListFilters,
} from "@/features/platform/audit-policy";
import { platformGetCollection } from "@/features/platform/client";
import { formatAppDateTime } from "@/lib/date";
import { DigitInput } from "@/lib/ui/digit-input";
import { IdentifierValue } from "@/lib/ui/identifier";
import type { PlatformAuditItem } from "@/types/api";

const INITIAL_FILTERS: AuditListFilters = {
  targetGymId: "",
  action: "",
  outcome: "",
  limit: "25",
};

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function formatWhen(value: string): string {
  return formatAppDateTime(value);
}

function outcomeLabel(outcome: string): string {
  if (outcome === "SUCCESS") return "Success";
  if (outcome === "DENIED") return "Denied";
  if (outcome === "ERROR") return "Error";
  return outcome;
}

function outcomeTone(outcome: string): string {
  if (outcome === "SUCCESS") return "bg-emerald-100 text-emerald-800";
  if (outcome === "DENIED") return "bg-amber-100 text-amber-900";
  if (outcome === "ERROR") return "bg-red-100 text-red-800";
  return "bg-slate-100 text-slate-700";
}

function outcomeDot(outcome: string): string {
  if (outcome === "SUCCESS") return "bg-emerald-500";
  if (outcome === "DENIED") return "bg-amber-500";
  if (outcome === "ERROR") return "bg-red-500";
  return "bg-slate-300";
}

function isInitial(filters: AuditListFilters): boolean {
  return (
    filters.targetGymId === INITIAL_FILTERS.targetGymId &&
    filters.action === INITIAL_FILTERS.action &&
    filters.outcome === INITIAL_FILTERS.outcome &&
    filters.limit === INITIAL_FILTERS.limit
  );
}

function isEmptyPayload(value: string): boolean {
  return value === "null" || value === "{}" || value === "[]";
}

function FilterField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <label className="field">
      <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
        {tx(label)}
      </span>
      {children}
    </label>
  );
}

function OutcomePill({ outcome }: { outcome: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-0.5 text-[0.7rem] font-bold tracking-wide ${outcomeTone(outcome)}`}
    >
      {outcomeLabel(outcome)}
    </span>
  );
}

function Blank() {
  return <span className="font-medium text-slate-400">—</span>;
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  const tx = useTx();
  return (
    <div className="min-w-0">
      <dt className="text-[0.68rem] font-bold tracking-[0.14em] text-slate-400 uppercase">
        {tx(label)}
      </dt>
      <dd className="mt-1 text-sm font-semibold break-all text-slate-900">
        {children}
      </dd>
    </div>
  );
}

function ValuePanel({ title, value }: { title: string; value: string }) {
  const tx = useTx();
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <h3 className="border-b border-slate-100 px-3 py-2 text-xs font-bold tracking-wide text-slate-500 uppercase">
        {tx(title)}
      </h3>
      {isEmptyPayload(value) ? (
        <p className="px-3 py-6 text-sm text-slate-500">
          {tx("Nothing recorded.")}
        </p>
      ) : (
        <pre className="max-h-72 overflow-auto px-3 py-3 font-mono text-[0.8rem] leading-6 break-words whitespace-pre-wrap text-slate-800">
          {value}
        </pre>
      )}
    </section>
  );
}

function Separator() {
  return (
    <span aria-hidden="true" className="text-slate-300">
      ·
    </span>
  );
}

export function PlatformAuditLog({ canView }: { canView: boolean }) {
  const tx = useTx();
  const [draft, setDraft] = useState(INITIAL_FILTERS);
  const [applied, setApplied] = useState(INITIAL_FILTERS);
  const [events, setEvents] = useState<PlatformAuditItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(canView);
  const [error, setError] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const load = useCallback(
    async (filters: AuditListFilters, after?: string) => {
      const request = buildAuditListPath(
        after ? { ...filters, after } : filters,
      );
      if (!request.ok) {
        setError(request.message);
        return;
      }
      setError(null);
      setLoading(true);
      try {
        const body = await platformGetCollection<PlatformAuditItem>(
          request.path,
        );
        setEvents((current) =>
          after ? [...current, ...body.data] : body.data,
        );
        setNextAfter(body.meta.next_after ?? null);
        setSelectedId((current) => {
          if (after) return current;
          return body.data[0]?.id ?? null;
        });
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load audit events.",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!canView) return;
    let active = true;
    void (async () => {
      const request = buildAuditListPath(INITIAL_FILTERS);
      if (!request.ok) return;
      try {
        const body = await platformGetCollection<PlatformAuditItem>(
          request.path,
        );
        if (!active) return;
        setEvents(body.data);
        setNextAfter(body.meta.next_after ?? null);
        setSelectedId(body.data[0]?.id ?? null);
      } catch (reason) {
        if (!active) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load audit events.",
        );
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [canView]);

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = buildAuditListPath(draft);
    if (!request.ok) {
      setError(request.message);
      return;
    }
    setApplied(draft);
    void load(draft);
  }

  function clearFilters() {
    const reload = !isInitial(applied);
    setDraft(INITIAL_FILTERS);
    setError(null);
    if (!reload) return;
    setApplied(INITIAL_FILTERS);
    void load(INITIAL_FILTERS);
  }

  if (!canView) {
    return (
      <p className="rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center text-sm font-semibold text-slate-700 shadow-sm">
        {" "}
        {tx("You do not have access to platform audit logs.")}{" "}
      </p>
    );
  }

  const shown =
    loading && events.length === 0
      ? tx("Fetching the latest events.")
      : events.length === 1
        ? tx("1 event on this page")
        : tx("{count} events on this page", { count: events.length });
  const filtersDirty = !isInitial(draft) || !isInitial(applied);

  return (
    <section
      aria-labelledby="platform-audit-activity-title"
      className={cardClass}
    >
      <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2
            className="text-lg font-bold text-slate-900"
            id="platform-audit-activity-title"
          >
            {" "}
            {tx("Activity")}{" "}
          </h2>
          <p className="text-xs font-medium text-slate-400">{shown}</p>
        </div>
      </div>

      <div className="flex items-center justify-end gap-3 border-b border-slate-100 px-4 py-3 lg:hidden">
        {!isInitial(applied) ? (
          <p className="mr-auto text-sm font-medium text-slate-600">
            {" "}
            {tx("Filters are on.")}{" "}
          </p>
        ) : null}
        <button
          aria-controls="platform-audit-filters"
          aria-expanded={showFilters}
          className="button button-cancel button-sm"
          onClick={() => setShowFilters((current) => !current)}
          type="button"
        >
          {showFilters ? tx("Hide filters") : tx("Filters")}
        </button>
      </div>
      <form
        aria-label={tx("Audit filters")}
        className={`border-b border-slate-100 bg-slate-50/80 px-4 py-4 sm:px-5 ${
          showFilters ? "block" : "hidden lg:block"
        }`}
        id="platform-audit-filters"
        onSubmit={applyFilters}
      >
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <FilterField label="Target gym ID">
            <input
              autoComplete="off"
              className="input placeholder:text-slate-400"
              disabled={loading}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  targetGymId: event.target.value,
                }))
              }
              placeholder={tx("Any gym")}
              spellCheck={false}
              value={draft.targetGymId}
            />
          </FilterField>
          <FilterField label="Action">
            <input
              autoComplete="off"
              className="input placeholder:text-slate-400"
              disabled={loading}
              maxLength={100}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  action: event.target.value,
                }))
              }
              placeholder={tx("Any action")}
              spellCheck={false}
              value={draft.action}
            />
          </FilterField>
          <FilterField label="Outcome">
            <select
              className="input"
              disabled={loading}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  outcome: event.target.value,
                }))
              }
              value={draft.outcome}
            >
              <option value="">{tx("All outcomes")}</option>
              <option value="SUCCESS">{tx("Success")}</option>
              <option value="DENIED">{tx("Denied")}</option>
              <option value="ERROR">{tx("Error")}</option>
            </select>
          </FilterField>
          <FilterField label="Page size">
            <DigitInput
              className="input placeholder:text-slate-400"
              disabled={loading}
              mode={{ kind: "integer" }}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  limit: event.target.value,
                }))
              }
              value={draft.limit}
            />
          </FilterField>
        </div>
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
          {!isInitial(applied) ? (
            <p className="text-xs font-medium text-slate-500 sm:mr-auto">
              {" "}
              {tx("These filters are applied to the list.")}{" "}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            {filtersDirty ? (
              <button
                className="button button-cancel"
                disabled={loading}
                onClick={clearFilters}
                type="button"
              >
                {" "}
                {tx("Clear filters")}{" "}
              </button>
            ) : null}
            <button className="button" disabled={loading} type="submit">
              {" "}
              {tx("Apply filters")}{" "}
            </button>
          </div>
        </div>
      </form>

      {error ? (
        <div className="px-4 pt-4 sm:px-5">
          <p className="error" role="alert">
            {tx(error)}
          </p>
        </div>
      ) : null}
      {loading && events.length === 0 ? (
        <div className="grid gap-3 p-4 sm:p-5">
          <p className="text-sm font-medium text-slate-500" role="status">
            {" "}
            {tx("Loading audit events.")}{" "}
          </p>
          {[0, 1, 2].map((item) => (
            <div
              className="h-16 animate-pulse rounded-xl bg-slate-100"
              key={item}
            />
          ))}
        </div>
      ) : null}
      {!loading && !error && events.length === 0 ? (
        <div className="px-6 py-16 text-center" role="status">
          <p className="font-semibold text-slate-800">
            {tx("No audit events found.")}
          </p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
            {" "}
            {tx("Try another gym, action, or outcome.")}{" "}
          </p>
        </div>
      ) : null}

      {events.length > 0 ? (
        <ol
          className={
            loading
              ? "divide-y divide-slate-100 opacity-60"
              : "divide-y divide-slate-100"
          }
        >
          {events.map((audit) => {
            const open = audit.id === selectedId;
            return (
              <li key={audit.id}>
                <button
                  aria-controls={`platform-audit-detail-${audit.id}`}
                  aria-expanded={open}
                  className={`flex w-full cursor-pointer items-start gap-3 border-0 px-4 py-3.5 text-left font-[inherit] sm:px-5 ${
                    open ? "bg-emerald-50/70" : "bg-white hover:bg-slate-50"
                  }`}
                  onClick={() => setSelectedId(open ? null : audit.id)}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={`mt-1.5 size-2.5 shrink-0 rounded-full ${outcomeDot(audit.outcome)}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold break-words text-slate-900 sm:text-base">
                      {audit.action}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-500">
                      <OutcomePill outcome={audit.outcome} />
                      <time
                        dateTime={audit.occurred_at}
                        title={formatWhen(audit.occurred_at)}
                      >
                        {formatWhen(audit.occurred_at)}
                      </time>
                      <Separator />
                      <span>{audit.actor_email}</span>
                      <Separator />
                      <span>{audit.module}</span>
                      {audit.target_gym_code ? (
                        <>
                          <Separator />
                          <span>{audit.target_gym_code}</span>
                        </>
                      ) : null}
                    </span>
                  </span>
                  <span
                    aria-hidden="true"
                    className={`mt-0.5 text-lg leading-none ${
                      open ? "rotate-90 text-emerald-700" : "text-slate-300"
                    }`}
                  >
                    ›
                  </span>
                </button>
                {open ? (
                  <div
                    className="border-t border-emerald-100 bg-emerald-50/40 px-4 py-4 sm:px-5"
                    id={`platform-audit-detail-${audit.id}`}
                  >
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                      <dl className="grid gap-4 sm:grid-cols-2">
                        <Meta label="Target type">{audit.target_type}</Meta>
                        <Meta label="Target gym">
                          {audit.target_gym_code ? (
                            audit.target_gym_code
                          ) : (
                            <Blank />
                          )}
                        </Meta>
                        <Meta label="Target gym ID">
                          {audit.target_gym_id ? (
                            <IdentifierValue value={audit.target_gym_id} />
                          ) : (
                            <Blank />
                          )}
                        </Meta>
                        <Meta label="Target ID">
                          {audit.target_id ? (
                            <IdentifierValue value={audit.target_id} />
                          ) : (
                            <Blank />
                          )}
                        </Meta>
                        <Meta label="Request ID">
                          <IdentifierValue value={audit.request_id} />
                        </Meta>
                      </dl>
                      <div className="mt-4 grid gap-3 lg:grid-cols-2">
                        <ValuePanel
                          title={tx("Previous values")}
                          value={formatAuditValue(audit.old_values)}
                        />
                        <ValuePanel
                          title={tx("New values")}
                          value={formatAuditValue(audit.new_values)}
                        />
                      </div>
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}

      {nextAfter ? (
        <div className="border-t border-slate-100 px-4 py-4 text-center sm:px-5">
          <button
            className="button"
            disabled={loading}
            onClick={() => void load(applied, nextAfter)}
            type="button"
          >
            {loading ? tx("Loading…") : tx("Load more")}
          </button>
        </div>
      ) : null}
    </section>
  );
}

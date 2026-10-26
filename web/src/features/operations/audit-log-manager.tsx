"use client";

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  buildTenantAuditListPath,
  formatTenantAuditValue,
  tenantAuditScopeLabel,
  type TenantAuditFilters,
} from "@/features/operations/audit-policy";
import { listAuditLogs } from "@/features/operations/client";
import { IdentifierValue } from "@/lib/ui/identifier";
import type { TenantAuditItem, TenantAuditMeta } from "@/types/api";

const INITIAL_FILTERS: TenantAuditFilters = {
  action: "",
  module: "",
  outcome: "",
  targetType: "",
  targetId: "",
  limit: "25",
};

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
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

function isInitial(filters: TenantAuditFilters): boolean {
  return (
    filters.action === INITIAL_FILTERS.action &&
    filters.module === INITIAL_FILTERS.module &&
    filters.outcome === INITIAL_FILTERS.outcome &&
    filters.targetType === INITIAL_FILTERS.targetType &&
    filters.targetId === INITIAL_FILTERS.targetId &&
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
  return (
    <label className="field">
      <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
        {label}
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
  return (
    <div className="min-w-0">
      <dt className="text-[0.68rem] font-bold tracking-[0.14em] text-slate-400 uppercase">
        {label}
      </dt>
      <dd className="mt-1 text-sm font-semibold break-all text-slate-900">
        {children}
      </dd>
    </div>
  );
}

function ValuePanel({ title, value }: { title: string; value: string }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <h3 className="border-b border-slate-100 px-3 py-2 text-xs font-bold tracking-wide text-slate-500 uppercase">
        {title}
      </h3>
      {isEmptyPayload(value) ? (
        <p className="px-3 py-6 text-sm text-slate-500">Nothing recorded.</p>
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

export function TenantAuditLog({ canView }: { canView: boolean }) {
  const [draft, setDraft] = useState(INITIAL_FILTERS);
  const [applied, setApplied] = useState(INITIAL_FILTERS);
  const [events, setEvents] = useState<TenantAuditItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scope, setScope] = useState<TenantAuditMeta["scope"] | null>(null);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(canView);
  const [error, setError] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const load = useCallback(
    async (filters: TenantAuditFilters, after?: string) => {
      const request = buildTenantAuditListPath(
        after ? { ...filters, after } : filters,
      );
      if (!request.ok) {
        setError(request.message);
        return;
      }
      setError(null);
      setLoading(true);
      try {
        const body = await listAuditLogs(
          after ? { ...filters, after } : filters,
        );
        setEvents((current) =>
          after ? [...current, ...body.data] : body.data,
        );
        setScope(body.meta.scope);
        setNextAfter(body.meta.next_after);
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
      const request = buildTenantAuditListPath(INITIAL_FILTERS);
      if (!request.ok) return;
      try {
        const body = await listAuditLogs(INITIAL_FILTERS);
        if (!active) return;
        setEvents(body.data);
        setScope(body.meta.scope);
        setNextAfter(body.meta.next_after);
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
    const request = buildTenantAuditListPath(draft);
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
        You do not have access to audit logs.
      </p>
    );
  }

  const shown =
    loading && events.length === 0
      ? "Fetching the latest events."
      : events.length === 1
        ? "1 event on this page"
        : `${events.length} events on this page`;
  const filtersDirty = !isInitial(draft) || !isInitial(applied);

  return (
    <section aria-labelledby="audit-activity-title" className={cardClass}>
      <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2
            className="text-lg font-bold text-slate-900"
            id="audit-activity-title"
          >
            Activity
          </h2>
          {scope ? (
            <span
              className={
                scope === "SELF"
                  ? "rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-950"
                  : "rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-600"
              }
              data-scope={scope}
            >
              {tenantAuditScopeLabel(scope)}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm leading-6 text-slate-500">
          Newest events are first. Open one to read the change.
        </p>
        {scope === "SELF" ? (
          <p className="mt-1 text-sm font-medium text-amber-800">
            Showing only actions recorded for your membership.
          </p>
        ) : null}
        <p className="mt-1 text-xs font-medium text-slate-400">{shown}</p>
      </div>

      <div className="flex items-center justify-end gap-3 border-b border-slate-100 px-4 py-3 lg:hidden">
        {!isInitial(applied) ? (
          <p className="mr-auto text-sm font-medium text-slate-600">
            Filters are on.
          </p>
        ) : null}
        <button
          aria-controls="audit-filters"
          aria-expanded={showFilters}
          className="button button-cancel button-sm"
          onClick={() => setShowFilters((current) => !current)}
          type="button"
        >
          {showFilters ? "Hide filters" : "Filters"}
        </button>
      </div>
      <form
        aria-label="Audit filters"
        className={`border-b border-slate-100 bg-slate-50/80 px-4 py-4 sm:px-5 ${
          showFilters ? "block" : "hidden lg:block"
        }`}
        id="audit-filters"
        onSubmit={applyFilters}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
              placeholder="Any action"
              spellCheck={false}
              value={draft.action}
            />
          </FilterField>
          <FilterField label="Module">
            <input
              autoComplete="off"
              className="input placeholder:text-slate-400"
              disabled={loading}
              maxLength={50}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  module: event.target.value,
                }))
              }
              placeholder="Any module"
              spellCheck={false}
              value={draft.module}
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
              <option value="">All outcomes</option>
              <option value="SUCCESS">Success</option>
              <option value="DENIED">Denied</option>
              <option value="ERROR">Error</option>
            </select>
          </FilterField>
          <FilterField label="Target type">
            <input
              autoComplete="off"
              className="input placeholder:text-slate-400"
              disabled={loading}
              maxLength={100}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  targetType: event.target.value,
                }))
              }
              placeholder="Any type"
              spellCheck={false}
              value={draft.targetType}
            />
          </FilterField>
          <FilterField label="Target ID">
            <input
              autoComplete="off"
              className="input placeholder:text-slate-400"
              disabled={loading}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  targetId: event.target.value,
                }))
              }
              placeholder="Any identifier"
              spellCheck={false}
              value={draft.targetId}
            />
          </FilterField>
          <FilterField label="Page size">
            <input
              className="input placeholder:text-slate-400"
              disabled={loading}
              inputMode="numeric"
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
              These filters are applied to the list.
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
                Clear filters
              </button>
            ) : null}
            <button className="button" disabled={loading} type="submit">
              Apply filters
            </button>
          </div>
        </div>
      </form>

      {error ? (
        <div className="px-4 pt-4 sm:px-5">
          <p className="error" role="alert">
            {error}
          </p>
        </div>
      ) : null}
      {loading && events.length === 0 ? (
        <div className="grid gap-3 p-4 sm:p-5">
          <p className="text-sm font-medium text-slate-500" role="status">
            Loading audit events.
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
          <p className="font-semibold text-slate-800">No audit events found.</p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
            Try another action, module, or outcome.
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
                  aria-controls={`audit-detail-${audit.id}`}
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
                        title={audit.occurred_at}
                      >
                        {formatWhen(audit.occurred_at)}
                      </time>
                      <Separator />
                      <span>{audit.module}</span>
                      <Separator />
                      <span>{audit.target_type}</span>
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
                    id={`audit-detail-${audit.id}`}
                  >
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                      <dl className="grid gap-4 sm:grid-cols-2">
                        <Meta label="Actor membership">
                          {audit.actor_membership_id ? (
                            <IdentifierValue
                              value={audit.actor_membership_id}
                            />
                          ) : (
                            <Blank />
                          )}
                        </Meta>
                        <Meta label="Impersonator membership">
                          {audit.impersonator_membership_id ? (
                            <IdentifierValue
                              value={audit.impersonator_membership_id}
                            />
                          ) : (
                            <Blank />
                          )}
                        </Meta>
                        <Meta label="Target ID">
                          <IdentifierValue value={audit.target_id} />
                        </Meta>
                        <Meta label="Request ID">
                          <IdentifierValue value={audit.request_id} />
                        </Meta>
                      </dl>
                      <div className="mt-4 grid gap-3 lg:grid-cols-2">
                        <ValuePanel
                          title="Previous values"
                          value={formatTenantAuditValue(audit.old_values)}
                        />
                        <ValuePanel
                          title="New values"
                          value={formatTenantAuditValue(audit.new_values)}
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
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

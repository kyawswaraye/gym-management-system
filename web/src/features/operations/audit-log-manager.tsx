"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

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

function display(value: string | null): string {
  return value && value.length > 0 ? value : "None";
}

export function TenantAuditLog({ canView }: { canView: boolean }) {
  const [draft, setDraft] = useState(INITIAL_FILTERS);
  const [applied, setApplied] = useState(INITIAL_FILTERS);
  const [events, setEvents] = useState<TenantAuditItem[]>([]);
  const [scope, setScope] = useState<TenantAuditMeta["scope"] | null>(null);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(canView);
  const [error, setError] = useState<string | null>(null);

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

  if (!canView) {
    return <p>You do not have access to audit logs.</p>;
  }

  return (
    <div className="grid gap-5">
      {scope && (
        <p className="font-medium" data-scope={scope}>
          {tenantAuditScopeLabel(scope)}
        </p>
      )}
      {scope === "SELF" && (
        <p className="muted">
          Showing only actions recorded for your membership.
        </p>
      )}
      <form className="grid gap-3 md:grid-cols-2" onSubmit={applyFilters}>
        <label className="field">
          <span>Action</span>
          <input
            className="input"
            value={draft.action}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                action: event.target.value,
              }))
            }
            maxLength={100}
            disabled={loading}
          />
        </label>
        <label className="field">
          <span>Module</span>
          <input
            className="input"
            value={draft.module}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                module: event.target.value,
              }))
            }
            maxLength={50}
            disabled={loading}
          />
        </label>
        <label className="field">
          <span>Outcome</span>
          <select
            className="input"
            value={draft.outcome}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                outcome: event.target.value,
              }))
            }
            disabled={loading}
          >
            <option value="">All outcomes</option>
            <option value="SUCCESS">SUCCESS</option>
            <option value="DENIED">DENIED</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <label className="field">
          <span>Target type</span>
          <input
            className="input"
            value={draft.targetType}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                targetType: event.target.value,
              }))
            }
            maxLength={100}
            disabled={loading}
          />
        </label>
        <label className="field">
          <span>Target ID</span>
          <input
            className="input"
            value={draft.targetId}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                targetId: event.target.value,
              }))
            }
            spellCheck={false}
            disabled={loading}
          />
        </label>
        <label className="field">
          <span>Page size</span>
          <input
            className="input"
            inputMode="numeric"
            value={draft.limit}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                limit: event.target.value,
              }))
            }
            disabled={loading}
          />
        </label>
        <button
          className="button md:col-span-2"
          type="submit"
          disabled={loading}
        >
          Apply filters
        </button>
      </form>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && events.length === 0 && (
        <p className="muted">Loading audit events.</p>
      )}
      {!loading && !error && events.length === 0 && (
        <p className="muted">No audit events found.</p>
      )}

      <ol className="grid gap-3">
        {events.map((audit) => (
          <li
            key={audit.id}
            className="rounded-lg border border-slate-200 bg-white p-4 text-sm"
            aria-label={`Audit event ${audit.id}`}
          >
            <div className="flex flex-wrap justify-between gap-2">
              <h2 className="text-base font-bold">{audit.action}</h2>
              <time dateTime={audit.occurred_at}>{audit.occurred_at}</time>
            </div>
            <dl className="mt-3 grid gap-2 md:grid-cols-2">
              <div>
                <dt className="font-medium">Actor membership</dt>
                <dd>
                  <IdentifierValue value={display(audit.actor_membership_id)} />
                </dd>
              </div>
              <div>
                <dt className="font-medium">Impersonator membership</dt>
                <dd>
                  <IdentifierValue
                    value={display(audit.impersonator_membership_id)}
                  />
                </dd>
              </div>
              <div>
                <dt className="font-medium">Outcome</dt>
                <dd>{audit.outcome}</dd>
              </div>
              <div>
                <dt className="font-medium">Module</dt>
                <dd>{audit.module}</dd>
              </div>
              <div>
                <dt className="font-medium">Target type</dt>
                <dd>{audit.target_type}</dd>
              </div>
              <div>
                <dt className="font-medium">Target ID</dt>
                <dd>
                  <IdentifierValue value={audit.target_id} />
                </dd>
              </div>
              <div>
                <dt className="font-medium">Request ID</dt>
                <dd>
                  <IdentifierValue value={audit.request_id} />
                </dd>
              </div>
            </dl>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <div>
                <h3 className="font-medium">Previous values</h3>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words">
                  {formatTenantAuditValue(audit.old_values)}
                </pre>
              </div>
              <div>
                <h3 className="font-medium">New values</h3>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words">
                  {formatTenantAuditValue(audit.new_values)}
                </pre>
              </div>
            </div>
          </li>
        ))}
      </ol>

      {nextAfter && (
        <button
          className="button"
          type="button"
          disabled={loading}
          onClick={() => void load(applied, nextAfter)}
        >
          Load more
        </button>
      )}
    </div>
  );
}

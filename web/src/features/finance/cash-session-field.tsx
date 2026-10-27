"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState } from "react";

import { listCashSessions } from "@/features/finance/client";
import { financeError } from "@/features/finance/errors";
import {
  collectLookupPages,
  LOOKUP_PAGE_LIMIT,
} from "@/features/finance/paging";
import { Field, SelectInput } from "@/features/finance/ui";
import type { CashSession } from "@/types/api";

export function useOpenCashSessions(businessDate: string) {
  const [sessions, setSessions] = useState<CashSession[]>([]);
  const [error, setError] = useState("");
  const [bounded, setBounded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadedFor, setLoadedFor] = useState("");

  useEffect(() => {
    if (!businessDate) {
      const timer = window.setTimeout(() => {
        setSessions([]);
        setError("");
        setBounded(false);
        setLoading(false);
        setLoadedFor("");
      }, 0);
      return () => window.clearTimeout(timer);
    }
    const requested = businessDate;
    let active = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      void collectLookupPages((after) => {
        if (!active) {
          return Promise.resolve({
            data: [],
            meta: { has_more: false, next_after: null },
          });
        }
        return listCashSessions({
          status: "OPEN",
          business_date: requested,
          limit: LOOKUP_PAGE_LIMIT,
          ...(after ? { after } : {}),
        });
      })
        .then((result) => {
          if (!active) return;
          const failed = Boolean(result.error);
          setSessions(failed ? [] : result.items);
          setBounded(result.bounded);
          setError(result.error);
          setLoadedFor(requested);
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setSessions([]);
          setBounded(false);
          setError(financeError(caught, "Open sessions could not be loaded."));
          setLoadedFor(requested);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [businessDate]);

  const current = loadedFor === businessDate;
  return {
    sessions: current ? sessions : [],
    error: current ? error : "",
    bounded: current && bounded,
    hasMore: current && bounded,
    pending: businessDate !== "" && (!current || loading),
  };
}

export function CashSessionSelect({
  sessions,
  currency,
  label,
  name = "cash_session_id",
  error,
  collectionError,
  pending = false,
  required = true,
  resetKey,
}: {
  sessions: readonly CashSession[];
  currency: string;
  label: string;
  name?: string;
  error?: string | undefined;
  collectionError?: string | undefined;
  pending?: boolean;
  required?: boolean;
  resetKey?: string;
}) {
  const tx = useTx();
  const matches = collectionError
    ? []
    : sessions.filter(
        (session) => session.status === "OPEN" && session.currency === currency,
      );
  return (
    <div className="grid gap-1">
      {pending ? (
        <p className="muted" role="status">
          {" "}
          {tx("Loading open sessions.")}{" "}
        </p>
      ) : null}
      {collectionError ? (
        <p className="error" role="alert">
          {collectionError}
        </p>
      ) : null}
      <Field error={error} label={label}>
        <SelectInput key={resetKey} name={name} required={required}>
          <option value="">{tx("Select an open session")}</option>
          {matches.map((session) => (
            <option key={session.id} value={session.id}>
              {session.register_code} · {session.business_date} ·{" "}
              {tx(session.currency)}
            </option>
          ))}
        </SelectInput>
      </Field>
      <p className="text-sm text-slate-500">
        {" "}
        {tx("Only open sessions returned for your access are listed.")}{" "}
      </p>
    </div>
  );
}

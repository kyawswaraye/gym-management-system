"use client";

import { useEffect, useState } from "react";

import { listCashSessions } from "@/features/finance/client";
import { financeError } from "@/features/finance/errors";
import { Field, SelectInput } from "@/features/finance/ui";
import type { CashSession } from "@/types/api";

export function useOpenCashSessions(businessDate: string) {
  const [sessions, setSessions] = useState<CashSession[]>([]);
  const [error, setError] = useState("");
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    if (!businessDate) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void listCashSessions({
        status: "OPEN",
        business_date: businessDate,
        limit: "100",
      })
        .then((page) => {
          if (!active) return;
          setSessions(page.data);
          setHasMore(page.meta.has_more);
          setError("");
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setSessions([]);
          setHasMore(false);
          setError(financeError(caught, "Open sessions could not be loaded."));
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [businessDate]);

  return { sessions, error, hasMore };
}

export function CashSessionSelect({
  sessions,
  currency,
  label,
  name = "cash_session_id",
  error,
  required = true,
}: {
  sessions: readonly CashSession[];
  currency: string;
  label: string;
  name?: string;
  error?: string | undefined;
  required?: boolean;
}) {
  const matches = sessions.filter(
    (session) => session.status === "OPEN" && session.currency === currency,
  );
  return (
    <div className="grid gap-1">
      <Field error={error} label={label}>
        <SelectInput name={name} required={required}>
          <option value="">Select an open session</option>
          {matches.map((session) => (
            <option key={session.id} value={session.id}>
              {session.register_code} · {session.business_date} ·{" "}
              {session.currency}
            </option>
          ))}
        </SelectInput>
      </Field>
      <p className="text-sm text-slate-500">
        Only open sessions returned for your access are listed.
      </p>
    </div>
  );
}

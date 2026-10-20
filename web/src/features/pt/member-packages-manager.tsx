"use client";

import { useRef, useState } from "react";

import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import {
  adjustPackageLedger,
  grantMemberPackage,
  listMemberPackages,
  listPackageLedger,
  listServicePackages,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { useLookupOptions } from "@/features/pt/lookup";
import { MemberSearch, type PickedMember } from "@/features/pt/member-search";
import type {
  MemberServicePackage,
  ServicePackage,
  ServiceSessionLedger,
} from "@/types/api";

const PAGE = "25";

export function MemberPackagesManager({
  canAdjust,
  today,
}: {
  canAdjust: boolean;
  today: string;
}) {
  const [status, setStatus] = useState("");
  const [packageType, setPackageType] = useState("");
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [selected, setSelected] = useState<MemberServicePackage | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [mode, setMode] = useState<"grant" | "correct" | null>(null);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const grants = useCursorPages<MemberServicePackage>(
    `grants:${status}:${packageType}:${memberFilter?.id ?? ""}`,
    (after) =>
      listMemberPackages({
        limit: PAGE,
        ...(after ? { after } : {}),
        ...(status ? { status } : {}),
        ...(packageType ? { package_type: packageType } : {}),
        ...(memberFilter ? { member_id: memberFilter.id } : {}),
      }),
  );
  const ledger = useCursorPages<ServiceSessionLedger>(
    selected ? `ledger:${selected.id}` : "ledger:none",
    (after) =>
      selected
        ? listPackageLedger(selected.id, {
            limit: PAGE,
            ...(after ? { after } : {}),
          })
        : Promise.resolve({
            data: [],
            meta: { has_more: false, next_after: null },
          }),
  );
  const catalog = useLookupOptions<ServicePackage>(
    "grant-catalog",
    (after) =>
      listServicePackages({
        limit: "100",
        is_active: "true",
        ...(after ? { after } : {}),
      }),
    mode === "grant",
  );

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  function chooseGrant(row: MemberServicePackage) {
    setSelected(row);
    setMode((current) => (current === "correct" ? null : current));
  }

  return (
    <div className="grid gap-4">
      <Alert message={error || grants.error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Field label="Grant status">
          <SelectInput
            onChange={(event) => {
              setStatus(event.target.value);
              setSelected(null);
            }}
            value={status}
          >
            <option value="">All</option>
            <option value="ACTIVE">ACTIVE</option>
            <option value="EXHAUSTED">EXHAUSTED</option>
            <option value="EXPIRED">EXPIRED</option>
            <option value="VOID">VOID</option>
          </SelectInput>
        </Field>
        <Field label="Package type">
          <SelectInput
            onChange={(event) => {
              setPackageType(event.target.value);
              setSelected(null);
            }}
            value={packageType}
          >
            <option value="">All</option>
            <option value="PT">PT</option>
            <option value="CLASS">CLASS</option>
          </SelectInput>
        </Field>
      </div>
      <MemberSearch
        action="Search grant members"
        label="Filter grants by member"
        onSelect={(picked) => {
          setMemberFilter(picked);
          setSelected(null);
        }}
        selected={memberFilter}
      />
      {memberFilter ? (
        <button
          className="underline"
          onClick={() => {
            setMemberFilter(null);
            setSelected(null);
          }}
          type="button"
        >
          Clear member filter
        </button>
      ) : null}
      {grants.pending ? <Loading label="Loading grants." /> : null}
      {!grants.pending && grants.rows.length === 0 ? (
        <Empty label="No grants match." />
      ) : (
        <ul className="grid gap-3">
          {grants.rows.map((row) => (
            <li className="rounded border border-slate-200 p-3" key={row.id}>
              <button
                className="underline"
                onClick={() => chooseGrant(row)}
                type="button"
              >
                {row.package_code} · {row.package_name} · member {row.member_id}
              </button>
              <p className="muted">
                {row.status} · remaining {row.remaining_sessions} of{" "}
                {row.granted_sessions} · {row.valid_from} to {row.valid_until} ·
                package {row.service_package_id}
              </p>
            </li>
          ))}
        </ul>
      )}
      <CursorControls
        bounded={grants.bounded}
        hasMore={grants.hasMore}
        index={grants.index}
        onNext={() => void grants.goNext()}
        onPrevious={() => void grants.goPrevious()}
        pending={grants.pending}
      />
      <section aria-labelledby="ledger-title" className="grid gap-2">
        <h2 className="text-xl font-semibold" id="ledger-title">
          Session ledger
        </h2>
        {selected && ledger.pending ? (
          <Loading label="Loading the ledger." />
        ) : null}
        <Alert message={ledger.error} />
        {!selected ? (
          <Empty label="Choose a grant to read its ledger." />
        ) : null}
        {selected && !ledger.pending && ledger.rows.length === 0 ? (
          <Empty label="This grant has no ledger rows." />
        ) : (
          <ul className="grid gap-2">
            {ledger.rows.map((entry) => (
              <li key={entry.id}>
                {entry.reason} · {entry.session_delta} · {entry.occurred_at}
                {entry.pt_booking_id
                  ? ` · PT booking ${entry.pt_booking_id}`
                  : ""}
                {entry.class_booking_id
                  ? ` · class booking ${entry.class_booking_id}`
                  : ""}
              </li>
            ))}
          </ul>
        )}
        {selected ? (
          <CursorControls
            bounded={ledger.bounded}
            hasMore={ledger.hasMore}
            index={ledger.index}
            onNext={() => void ledger.goNext()}
            onPrevious={() => void ledger.goPrevious()}
            pending={ledger.pending}
          />
        ) : null}
        {canAdjust && selected ? (
          <button
            className="underline"
            onClick={() => setMode("correct")}
            type="button"
          >
            Correct ledger
          </button>
        ) : null}
      </section>
      {canAdjust ? (
        <button
          className="button"
          onClick={() => setMode("grant")}
          type="button"
        >
          Grant package
        </button>
      ) : null}
      {mode === "grant" && canAdjust ? (
        <form
          aria-labelledby="grant-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!member) {
              setError("Select an active member.");
              return;
            }
            const form = new FormData(event.currentTarget);
            const body = {
              member_id: member.id,
              service_package_id: String(form.get("service_package_id") ?? ""),
              business_date: String(form.get("business_date") ?? ""),
              complimentary: form.get("complimentary") === "true",
              reason: String(form.get("reason") ?? ""),
            };
            setPending(true);
            setError("");
            void grantMemberPackage(body, commandKey(JSON.stringify(body)))
              .then(() => {
                key.current = null;
                setNotice("Package granted.");
                setMode(null);
                grants.reload();
                ledger.reload();
              })
              .catch((caught: unknown) => {
                setError(phase5Error(caught, "The grant failed."));
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="grant-title">
            Grant package
          </h3>
          <MemberSearch
            disabled={pending}
            onSelect={setMember}
            selected={member}
          />
          <Alert message={catalog.error} />
          <Field label="Package">
            <SelectInput name="service_package_id" required>
              <option value="">Select a package</option>
              {catalog.items.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.type} · {row.code} · {row.name}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Business date">
            <TextInput
              defaultValue={today}
              name="business_date"
              required
              type="date"
            />
          </Field>
          <Field label="Complimentary">
            <SelectInput defaultValue="true" name="complimentary">
              <option value="true">Yes</option>
              <option value="false">No</option>
            </SelectInput>
          </Field>
          <p className="muted">
            A PT package must be complimentary. A paid PT grant comes from sale
            approval.
          </p>
          <ReasonField label="Reason" />
          <button className="button" disabled={pending} type="submit">
            Save grant
          </button>
        </form>
      ) : null}
      {mode === "correct" && canAdjust && selected ? (
        <form
          aria-labelledby="correct-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const body = {
              reverses_entry_id: String(form.get("reverses_entry_id") ?? ""),
              reason: String(form.get("reason") ?? ""),
            };
            setPending(true);
            setError("");
            void adjustPackageLedger(
              selected.id,
              body,
              commandKey(JSON.stringify(body)),
            )
              .then(async () => {
                key.current = null;
                setNotice("Ledger corrected.");
                setMode(null);
                grants.reload();
                ledger.reload();
              })
              .catch((caught: unknown) => {
                setError(phase5Error(caught, "The correction failed."));
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="correct-title">
            Correct ledger
          </h3>
          <Field label="Ledger entry to reverse">
            <SelectInput name="reverses_entry_id" required>
              <option value="">Select an entry</option>
              {ledger.rows
                .filter((entry) => entry.reason !== "GRANT")
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.reason} · {entry.session_delta}
                  </option>
                ))}
            </SelectInput>
          </Field>
          <p className="muted">
            Reversal choices are the ledger entries on this page, except grants.
            Move to another page to reach an earlier entry.
          </p>
          <ReasonField label="Correction reason" />
          <button className="button" disabled={pending} type="submit">
            Save correction
          </button>
        </form>
      ) : null}
    </div>
  );
}

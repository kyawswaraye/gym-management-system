"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { FormDialog } from "@/components/form-dialog";
import {
  listMemberPayments,
  operationGet,
  operationMutation,
} from "@/features/operations/client";
import { MemberPhoto } from "@/features/operations/member-photo";
import { MemberProfileFields } from "@/features/operations/member-profile-fields";
import {
  memberProfileFromMember,
  memberProfileSnapshot,
  memberUpdatePayload,
  type MemberProfileInput,
} from "@/features/operations/member-profile";
import {
  ArchiveMemberDialog,
  memberArchiveIdentity,
  RestoreMemberDialog,
} from "@/features/operations/member-restore-dialog";
import { formatInstant } from "@/lib/date";
import type {
  Member,
  MemberPaymentHistoryItem,
  MemberSummary,
} from "@/types/api";

const PANEL = "rounded-2xl bg-white p-5 shadow-sm sm:p-6";
const PANEL_TITLE = "text-base font-bold text-slate-900";

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700",
  EXPIRED: "bg-slate-100 text-slate-600",
  SUSPENDED: "bg-amber-50 text-amber-800",
  CANCELLED: "bg-rose-50 text-rose-700",
  ARCHIVED: "bg-slate-100 text-slate-500",
};

function appendPayments(
  current: MemberPaymentHistoryItem[],
  incoming: MemberPaymentHistoryItem[],
): MemberPaymentHistoryItem[] {
  const seen = new Set(current.map((payment) => payment.id));
  const next = incoming.filter((payment) => !seen.has(payment.id));
  return next.length === 0 ? current : [...current, ...next];
}

function PaymentHistory({ memberId }: { memberId: string }) {
  const tx = useTx();
  const [payments, setPayments] = useState<MemberPaymentHistoryItem[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const appliedCursors = useRef(new Set<string>());

  useEffect(() => {
    const request = ++generation.current;
    appliedCursors.current = new Set();
    const timer = window.setTimeout(() => {
      setPayments([]);
      setNextAfter(null);
      setHasMore(false);
      setLoading(true);
      setError("");
      void listMemberPayments(memberId)
        .then((page) => {
          if (request !== generation.current) return;
          setPayments(page.data);
          setNextAfter(page.meta.next_after);
          setHasMore(page.meta.has_more && Boolean(page.meta.next_after));
          setError("");
        })
        .catch((caught: unknown) => {
          if (request !== generation.current) return;
          setPayments([]);
          setNextAfter(null);
          setHasMore(false);
          setError(
            caught instanceof Error
              ? caught.message
              : "Unable to load payment history.",
          );
        })
        .finally(() => {
          if (request === generation.current) setLoading(false);
        });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      generation.current += 1;
    };
  }, [memberId]);

  async function loadOlder() {
    if (
      !hasMore ||
      !nextAfter ||
      paging ||
      appliedCursors.current.has(nextAfter)
    ) {
      return;
    }
    const cursor = nextAfter;
    const request = ++generation.current;
    appliedCursors.current.add(cursor);
    setPaging(true);
    setError("");
    try {
      const page = await listMemberPayments(memberId, cursor);
      if (request !== generation.current) return;
      setPayments((current) => appendPayments(current, page.data));
      const next = page.meta.next_after;
      if (!page.meta.has_more || !next || next === cursor) {
        setHasMore(false);
        setNextAfter(null);
      } else {
        setHasMore(true);
        setNextAfter(next);
      }
    } catch (caught) {
      appliedCursors.current.delete(cursor);
      if (request === generation.current) {
        setError(
          caught instanceof Error
            ? caught.message
            : "Unable to load payment history.",
        );
      }
    } finally {
      if (request === generation.current) setPaging(false);
    }
  }

  return (
    <section aria-labelledby="payment-history-title" className={PANEL}>
      <h2 className={PANEL_TITLE} id="payment-history-title">
        {tx("Payment history")}
      </h2>
      {loading ? (
        <p className="muted mt-3" role="status">
          {" "}
          {tx("Loading payment history.")}{" "}
        </p>
      ) : null}
      {error ? (
        <p className="error mt-3" role="alert">
          {tx(error)}
        </p>
      ) : null}
      {!loading && payments.length === 0 && !error ? (
        <p className="muted mt-3">{tx("No payments.")}</p>
      ) : null}
      {payments.length > 0 ? (
        <ul className="mt-4 divide-y divide-slate-100">
          {payments.map((payment) => (
            <li
              className="py-3 text-sm leading-6 text-slate-700 first:pt-1 last:pb-0"
              key={payment.id}
            >
              {payment.business_date} · {tx(payment.entry_type)} ·{" "}
              {tx(payment.purpose)} · {tx(payment.description)} ·{" "}
              {payment.amount} {tx(payment.currency)} · {tx(payment.method)} ·{" "}
              {tx(payment.status)}
              {payment.receipt_number
                ? ` · ${tx("Receipt {number}", { number: payment.receipt_number })}`
                : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {paging ? (
        <p className="muted mt-3" role="status">
          {" "}
          {tx("Loading older payments.")}{" "}
        </p>
      ) : null}
      {hasMore && nextAfter ? (
        <button
          className="button mt-3"
          disabled={paging}
          onClick={() => void loadOlder()}
          type="button"
        >
          {" "}
          {tx("Load older payments")}{" "}
        </button>
      ) : null}
    </section>
  );
}

export function MemberDetail({
  memberId,
  canArchive,
  canRestore = false,
  canUpdate,
  canViewPayments,
  canViewCheckIns = false,
  timeZone = "Asia/Yangon",
  canUploadPhoto = false,
  canViewPhoto = false,
  canDeletePhoto = false,
}: {
  memberId: string;
  canArchive: boolean;
  canRestore?: boolean;
  canUpdate: boolean;
  canViewPayments: boolean;
  canViewCheckIns?: boolean;
  timeZone?: string;
  canUploadPhoto?: boolean;
  canViewPhoto?: boolean;
  canDeletePhoto?: boolean;
}) {
  const tx = useTx();
  const [summary, setSummary] = useState<MemberSummary | null>(null);
  const [error, setError] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [activeMemberId, setActiveMemberId] = useState(memberId);
  const [draftState, setDraftState] = useState<{
    snapshot: string;
    draft: MemberProfileInput;
  } | null>(null);
  const archiveLocked = useRef(false);
  const restoreLocked = useRef(false);
  const saveLocked = useRef(false);
  const loadGeneration = useRef(0);
  const viewedMemberId = useRef(memberId);
  const mounted = useRef(true);

  useEffect(() => {
    viewedMemberId.current = memberId;
  }, [memberId]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  if (activeMemberId !== memberId) {
    setActiveMemberId(memberId);
    setSummary(null);
    setDraftState(null);
    setError("");
    setSaving(false);
    setArchiving(false);
    setArchiveOpen(false);
    setRestoring(false);
    setRestoreOpen(false);
  }

  const load = useCallback(async () => {
    const request = ++loadGeneration.current;
    try {
      const next = await operationGet<MemberSummary>(
        `members/${memberId}/summary/`,
      );
      if (request !== loadGeneration.current) return;
      setSummary(next);
      setError("");
    } catch (caught) {
      if (request !== loadGeneration.current) return;
      setError(
        caught instanceof Error ? caught.message : "Unable to load member.",
      );
    }
  }, [memberId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const memberForDraft = summary?.member ?? null;
  const profileSnapshot = memberForDraft
    ? memberProfileSnapshot(memberForDraft)
    : "";
  const draft =
    memberForDraft && draftState?.snapshot === profileSnapshot
      ? draftState.draft
      : memberForDraft
        ? memberProfileFromMember(memberForDraft)
        : null;
  if (memberForDraft && draft && draftState?.snapshot !== profileSnapshot) {
    setDraftState({ snapshot: profileSnapshot, draft });
  }

  function applyPhoto(assetId: string | null) {
    setSummary((current) =>
      current
        ? {
            ...current,
            member: { ...current.member, profile_photo_asset_id: assetId },
          }
        : current,
    );
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canUpdate || !draft || saveLocked.current) return;
    const payload = memberUpdatePayload(draft);
    if (!payload.first_name || !payload.last_name || !payload.join_date) {
      setError(tx("Enter a first name, last name, and join date."));
      return;
    }
    saveLocked.current = true;
    setSaving(true);
    setError("");
    try {
      await operationMutation<Member>(`members/${memberId}/`, payload, "PATCH");
      setEditing(false);
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to update member.",
      );
    } finally {
      saveLocked.current = false;
      setSaving(false);
    }
  }

  if (!summary && error) {
    return (
      <p className="error" role="alert">
        {tx(error)}
      </p>
    );
  }
  if (!summary || !draft) {
    return (
      <p className="muted" role="status">
        {" "}
        {tx("Loading member summary.")}{" "}
      </p>
    );
  }
  const member = summary.member;
  const memberName = `${member.first_name} ${member.last_name}`.trim();
  const payments = summary.payment_summary;
  const noFinalizedPayments =
    payments.total_charged === "0.00" && payments.total_refunded === "0.00";
  const showArchive = canArchive && member.status !== "ARCHIVED";
  const showRestore = canRestore && member.status === "ARCHIVED";
  const commandPending = archiving || restoring || saving;

  async function archiveMember() {
    if (archiveLocked.current || !showArchive) return;
    archiveLocked.current = true;
    setArchiveOpen(false);
    setArchiving(true);
    setError("");
    try {
      const archived = await operationMutation<Member>(
        `members/${memberId}/archive/`,
        {},
      );
      setSummary((current) =>
        current
          ? {
              ...current,
              member: archived,
              is_pt_client: archived.is_pt_client,
            }
          : current,
      );
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to archive member.",
      );
    } finally {
      archiveLocked.current = false;
      setArchiving(false);
    }
  }

  async function restoreMember() {
    if (restoreLocked.current || !showRestore) return;
    restoreLocked.current = true;
    setRestoreOpen(false);
    setRestoring(true);
    setError("");
    try {
      const restored = await operationMutation<Member>(
        `members/${memberId}/restore/`,
        {},
      );
      setSummary((current) =>
        current
          ? {
              ...current,
              member: restored,
              is_pt_client: restored.is_pt_client,
            }
          : current,
      );
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to restore member.",
      );
    } finally {
      restoreLocked.current = false;
      setRestoring(false);
    }
  }

  return (
    <>
      <div className="grid gap-5">
        <section className={PANEL}>
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
            <div className="sm:w-64 sm:shrink-0">
              <MemberPhoto
                assetId={member.profile_photo_asset_id}
                canDelete={canDeletePhoto}
                canUpload={canUploadPhoto}
                canView={canViewPhoto}
                memberId={memberId}
                memberName={memberName}
                onAttach={async (fileId) => {
                  const targetMemberId = memberId;
                  await operationMutation<Member>(
                    `members/${targetMemberId}/`,
                    { profile_photo_asset_id: fileId },
                    "PATCH",
                  );
                  if (
                    !mounted.current ||
                    viewedMemberId.current !== targetMemberId
                  )
                    return;
                  applyPhoto(fileId);
                  await load();
                }}
                onRemove={async () => {
                  applyPhoto(null);
                  await load();
                }}
              />
            </div>
            <div className="grid min-w-0 flex-1 content-start gap-4">
              <div>
                <p className="text-xs font-bold tracking-[0.16em] text-sky-700 uppercase">
                  {member.member_number}
                </p>
                <p className="mt-1 text-2xl font-black tracking-tight text-slate-900">
                  {memberName || member.member_number}
                </p>
              </div>
              {error && !editing ? (
                <p className="error" role="alert">
                  {tx(error)}
                </p>
              ) : null}
              <dl>
                <div className="flex items-center gap-3">
                  <dt className="text-sm text-slate-500">{tx("Status")}</dt>
                  <dd
                    className={`rounded-full px-3 py-1 text-xs font-bold ${STATUS_TONE[member.status] ?? "bg-slate-100 text-slate-600"}`}
                  >
                    {tx(member.status)}
                  </dd>
                </div>
              </dl>
              <div className="flex flex-wrap gap-3">
                {canUpdate ? (
                  <button
                    className="button button-edit"
                    disabled={commandPending}
                    onClick={() => setEditing(true)}
                    type="button"
                  >
                    {tx("Edit profile")}
                  </button>
                ) : null}
                {showArchive ? (
                  <button
                    className="button button-archive"
                    disabled={commandPending}
                    onClick={() => {
                      if (archiveLocked.current || commandPending) return;
                      setArchiveOpen(true);
                    }}
                    type="button"
                  >
                    {tx("Archive member")}
                  </button>
                ) : null}
                {showRestore ? (
                  <button
                    className="button button-success"
                    disabled={commandPending}
                    onClick={() => {
                      if (restoreLocked.current || commandPending) return;
                      setRestoreOpen(true);
                    }}
                    type="button"
                  >
                    {tx("Restore member")}
                  </button>
                ) : null}
              </div>
            </div>
          </div>
          {canUpdate && editing ? (
            <FormDialog
              onClose={() => {
                if (!saving) setEditing(false);
              }}
              onSubmit={save}
              pending={saving}
              title={tx("Edit profile")}
              wide
            >
              {error ? (
                <p className="error" role="alert">
                  {tx(error)}
                </p>
              ) : null}
              <MemberProfileFields
                disabled={commandPending}
                includeMemberNumber={false}
                onChange={(next) =>
                  setDraftState({ snapshot: profileSnapshot, draft: next })
                }
                value={draft}
              />
              <div>
                <button
                  className="button button-edit"
                  disabled={commandPending}
                  type="submit"
                >
                  {saving ? tx("Saving…") : tx("Save changes")}
                </button>
              </div>
            </FormDialog>
          ) : null}
        </section>
        {canUpdate ? null : (
          <section aria-labelledby="member-profile-fields" className={PANEL}>
            <h2 className={PANEL_TITLE} id="member-profile-fields">
              {tx("Profile")}
            </h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 [&_fieldset]:sm:col-span-2 [&>label:last-child]:sm:col-span-2">
              <MemberProfileFields
                disabled
                includeMemberNumber={false}
                onChange={() => undefined}
                value={draft}
              />
            </div>
          </section>
        )}
        <div className="grid gap-5 xl:grid-cols-2">
          <section aria-labelledby="current-membership-title" className={PANEL}>
            <h2 className={PANEL_TITLE} id="current-membership-title">
              {tx("Current membership")}
            </h2>
            {summary.current_membership ? (
              <div className="mt-4 grid gap-3">
                <p className="text-lg font-semibold text-slate-900">
                  {summary.current_membership.plan_name}:{" "}
                  {summary.current_membership.start_date} –{" "}
                  {summary.current_membership.expiry_date} (
                  {tx(summary.current_membership.payment_status)})
                </p>
                <p className="text-sm text-slate-500">
                  {summary.days_remaining === null
                    ? tx("Days remaining are unavailable.")
                    : summary.days_remaining === 1
                      ? tx("1 day remaining")
                      : tx("{count} days remaining", {
                          count: summary.days_remaining,
                        })}
                  {summary.is_pt_client ? tx("· PT client") : ""}
                </p>
              </div>
            ) : (
              <p className="mt-4 text-sm text-slate-500">
                {tx("No current membership.")}
              </p>
            )}
          </section>
          <section aria-labelledby="payment-summary-title" className={PANEL}>
            <h2 className={PANEL_TITLE} id="payment-summary-title">
              {tx("Payment summary")}
            </h2>
            {payments.available ? (
              <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ["Total charged", payments.total_charged],
                    ["Total refunded", payments.total_refunded],
                    ["Net paid", payments.net_paid],
                    [
                      "Outstanding membership balance",
                      payments.outstanding_membership_balance,
                    ],
                  ] as const
                ).map(([label, amount]) => (
                  <div className="rounded-xl bg-slate-50 px-4 py-3" key={label}>
                    <dt className="text-xs font-semibold text-slate-500">
                      {tx(label)}
                    </dt>
                    <dd className="mt-1 text-lg font-bold tracking-tight text-slate-900">
                      {amount} {tx(payments.currency)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-4 text-sm text-slate-500">
                {tx("Payment summary is not available.")}
              </p>
            )}
            {payments.available && noFinalizedPayments ? (
              <p className="mt-4 text-sm text-slate-500">
                {tx("No finalized payments.")}
              </p>
            ) : null}
          </section>
        </div>
        {canViewPayments ? <PaymentHistory memberId={memberId} /> : null}
        <div className="grid gap-5 xl:grid-cols-2">
          <section aria-labelledby="membership-history-title" className={PANEL}>
            <h2 className={PANEL_TITLE} id="membership-history-title">
              {tx("Membership history")}
            </h2>
            {summary.membership_history.length === 0 ? (
              <p className="mt-4 text-sm text-slate-500">
                {tx("No membership history.")}
              </p>
            ) : (
              <ul className="mt-4 divide-y divide-slate-100">
                {summary.membership_history.map((membership) => (
                  <li
                    className="py-3 text-sm leading-6 text-slate-700 first:pt-1 last:pb-0"
                    key={membership.id}
                  >
                    {membership.plan_name}: {membership.start_date} –{" "}
                    {membership.expiry_date} ({tx(membership.status)})
                  </li>
                ))}
              </ul>
            )}
            {summary.membership_history_has_more ? (
              <p className="mt-4 text-sm text-slate-500">
                {tx("Older memberships are not shown.")}
              </p>
            ) : null}
          </section>
          <section aria-labelledby="check-in-summary-title" className={PANEL}>
            <h2 className={PANEL_TITLE} id="check-in-summary-title">
              {tx("Check-ins")}
            </h2>
            {summary.check_in_summary.available ? (
              <dl className="mt-4 grid gap-3 sm:grid-cols-3 xl:grid-cols-1 2xl:grid-cols-3">
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <dt className="text-xs font-semibold text-slate-500">
                    {tx("Current visit")}
                  </dt>
                  <dd className="mt-1 text-sm font-bold text-slate-900">
                    {summary.check_in_summary.currently_checked_in
                      ? tx("Checked in")
                      : tx("Not checked in")}
                  </dd>
                </div>
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <dt className="text-xs font-semibold text-slate-500">
                    {tx("Last check-in")}
                  </dt>
                  <dd className="mt-1 text-sm font-bold text-slate-900">
                    {formatInstant(
                      summary.check_in_summary.last_check_in_at,
                      timeZone,
                    )}
                  </dd>
                </div>
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <dt className="text-xs font-semibold text-slate-500">
                    {tx("Last check-out")}
                  </dt>
                  <dd className="mt-1 text-sm font-bold text-slate-900">
                    {formatInstant(
                      summary.check_in_summary.last_check_out_at,
                      timeZone,
                    )}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-4 text-sm text-slate-500">
                {tx("Check-in summary is not available yet.")}
              </p>
            )}
            {canViewCheckIns ? (
              <p className="mt-4">
                <Link
                  className="text-sm font-semibold text-sky-700"
                  href="/check-ins"
                >
                  {tx("Open front desk")}
                </Link>
              </p>
            ) : null}
          </section>
        </div>
      </div>
      {archiveOpen ? (
        <ArchiveMemberDialog
          identity={memberArchiveIdentity(member)}
          onCancel={() => setArchiveOpen(false)}
          onConfirm={() => void archiveMember()}
        />
      ) : null}
      {restoreOpen ? (
        <RestoreMemberDialog
          identity={memberArchiveIdentity(member)}
          onCancel={() => setRestoreOpen(false)}
          onConfirm={() => void restoreMember()}
        />
      ) : null}
    </>
  );
}

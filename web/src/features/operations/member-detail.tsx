"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

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
import { formatInstant } from "@/lib/date";
import type {
  Member,
  MemberPaymentHistoryItem,
  MemberSummary,
} from "@/types/api";

function dayLabel(days: number): string {
  return days === 1 ? "1 day remaining" : `${days} days remaining`;
}

function memberArchiveIdentity(member: Member): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return name ? `${name} (${member.member_number})` : member.member_number;
}

function appendPayments(
  current: MemberPaymentHistoryItem[],
  incoming: MemberPaymentHistoryItem[],
): MemberPaymentHistoryItem[] {
  const seen = new Set(current.map((payment) => payment.id));
  const next = incoming.filter((payment) => !seen.has(payment.id));
  return next.length === 0 ? current : [...current, ...next];
}

function PaymentHistory({ memberId }: { memberId: string }) {
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
    <section aria-labelledby="payment-history-title" className="card p-5">
      <h2 className="font-bold" id="payment-history-title">
        Payment history
      </h2>
      {loading ? (
        <p className="muted mt-3" role="status">
          Loading payment history.
        </p>
      ) : null}
      {error ? (
        <p className="error mt-3" role="alert">
          {error}
        </p>
      ) : null}
      {!loading && payments.length === 0 && !error ? (
        <p className="muted mt-3">No payments.</p>
      ) : null}
      {payments.length > 0 ? (
        <ul className="mt-3 grid gap-2">
          {payments.map((payment) => (
            <li key={payment.id}>
              {payment.business_date} · {payment.entry_type} · {payment.purpose}{" "}
              · {payment.description} · {payment.amount} {payment.currency} ·{" "}
              {payment.method} · {payment.status}
              {payment.receipt_number
                ? ` · Receipt ${payment.receipt_number}`
                : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {paging ? (
        <p className="muted mt-3" role="status">
          Loading older payments.
        </p>
      ) : null}
      {hasMore && nextAfter ? (
        <button
          className="button mt-3"
          disabled={paging}
          onClick={() => void loadOlder()}
          type="button"
        >
          Load older payments
        </button>
      ) : null}
    </section>
  );
}

export function MemberDetail({
  memberId,
  canArchive,
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
  canUpdate: boolean;
  canViewPayments: boolean;
  canViewCheckIns?: boolean;
  timeZone?: string;
  canUploadPhoto?: boolean;
  canViewPhoto?: boolean;
  canDeletePhoto?: boolean;
}) {
  const [summary, setSummary] = useState<MemberSummary | null>(null);
  const [error, setError] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeMemberId, setActiveMemberId] = useState(memberId);
  const [draftState, setDraftState] = useState<{
    snapshot: string;
    draft: MemberProfileInput;
  } | null>(null);
  const archiveLocked = useRef(false);
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
      setError("Enter a first name, last name, and join date.");
      return;
    }
    saveLocked.current = true;
    setSaving(true);
    setError("");
    try {
      await operationMutation<Member>(`members/${memberId}/`, payload, "PATCH");
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
        {error}
      </p>
    );
  }
  if (!summary || !draft) {
    return (
      <p className="muted" role="status">
        Loading member summary.
      </p>
    );
  }
  const member = summary.member;
  const payments = summary.payment_summary;
  const noFinalizedPayments =
    payments.total_charged === "0.00" && payments.total_refunded === "0.00";
  const showArchive = canArchive && member.status !== "ARCHIVED";

  async function archiveMember() {
    if (archiveLocked.current || !showArchive) return;
    if (!window.confirm(`Archive member ${memberArchiveIdentity(member)}?`)) {
      return;
    }
    archiveLocked.current = true;
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

  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      <div className="card grid gap-4 p-5">
        <MemberPhoto
          assetId={member.profile_photo_asset_id}
          canDelete={canDeletePhoto}
          canUpload={canUploadPhoto}
          canView={canViewPhoto}
          memberId={memberId}
          memberName={`${member.first_name} ${member.last_name}`.trim()}
          onAttach={async (fileId) => {
            const targetMemberId = memberId;
            await operationMutation<Member>(
              `members/${targetMemberId}/`,
              { profile_photo_asset_id: fileId },
              "PATCH",
            );
            if (!mounted.current || viewedMemberId.current !== targetMemberId)
              return;
            applyPhoto(fileId);
            await load();
          }}
          onRemove={async () => {
            applyPhoto(null);
            await load();
          }}
        />
        <form className="grid gap-4" onSubmit={save}>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <p className="muted">{member.member_number}</p>
          <dl>
            <dt className="muted">Status</dt>
            <dd>{member.status}</dd>
          </dl>
          <MemberProfileFields
            disabled={!canUpdate || saving || archiving}
            includeMemberNumber={false}
            onChange={(next) =>
              setDraftState({ snapshot: profileSnapshot, draft: next })
            }
            value={draft}
          />
          {canUpdate && (
            <button
              className="button"
              disabled={saving || archiving}
              type="submit"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          )}
        </form>
        {showArchive ? (
          <button
            className="button"
            disabled={archiving || saving}
            onClick={() => void archiveMember()}
            type="button"
          >
            Archive member
          </button>
        ) : null}
      </div>
      <div className="grid gap-6">
        <section
          aria-labelledby="current-membership-title"
          className="card p-5"
        >
          <h2 className="font-bold" id="current-membership-title">
            Current membership
          </h2>
          {summary.current_membership ? (
            <div className="mt-3 grid gap-1">
              <p>
                {summary.current_membership.plan_name}:{" "}
                {summary.current_membership.start_date} –{" "}
                {summary.current_membership.expiry_date} (
                {summary.current_membership.payment_status})
              </p>
              <p className="muted">
                {summary.days_remaining === null
                  ? "Days remaining are unavailable."
                  : dayLabel(summary.days_remaining)}
                {summary.is_pt_client ? " · PT client" : ""}
              </p>
            </div>
          ) : (
            <p className="muted mt-3">No current membership.</p>
          )}
        </section>
        <section aria-labelledby="payment-summary-title" className="card p-5">
          <h2 className="font-bold" id="payment-summary-title">
            Payment summary
          </h2>
          {payments.available ? (
            <dl className="mt-3 grid gap-2">
              <div className="flex justify-between gap-4">
                <dt>Total charged</dt>
                <dd>
                  {payments.total_charged} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Total refunded</dt>
                <dd>
                  {payments.total_refunded} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Net paid</dt>
                <dd>
                  {payments.net_paid} {payments.currency}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>Outstanding membership balance</dt>
                <dd>
                  {payments.outstanding_membership_balance} {payments.currency}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="muted mt-3">Payment summary is not available.</p>
          )}
          {payments.available && noFinalizedPayments ? (
            <p className="muted mt-3">No finalized payments.</p>
          ) : null}
        </section>
        {canViewPayments ? <PaymentHistory memberId={memberId} /> : null}
        <section
          aria-labelledby="membership-history-title"
          className="card p-5"
        >
          <h2 className="font-bold" id="membership-history-title">
            Membership history
          </h2>
          {summary.membership_history.length === 0 ? (
            <p className="muted mt-3">No membership history.</p>
          ) : (
            <ul className="mt-3 grid gap-2">
              {summary.membership_history.map((membership) => (
                <li key={membership.id}>
                  {membership.plan_name}: {membership.start_date} –{" "}
                  {membership.expiry_date} ({membership.status})
                </li>
              ))}
            </ul>
          )}
          {summary.membership_history_has_more ? (
            <p className="muted mt-3">Older memberships are not shown.</p>
          ) : null}
        </section>
        <section aria-labelledby="check-in-summary-title" className="card p-5">
          <h2 className="font-bold" id="check-in-summary-title">
            Check-ins
          </h2>
          {summary.check_in_summary.available ? (
            <dl className="mt-3 grid gap-2">
              <div>
                <dt className="muted">Current visit</dt>
                <dd>
                  {summary.check_in_summary.currently_checked_in
                    ? "Checked in"
                    : "Not checked in"}
                </dd>
              </div>
              <div>
                <dt className="muted">Last check-in</dt>
                <dd>
                  {formatInstant(
                    summary.check_in_summary.last_check_in_at,
                    timeZone,
                  )}
                </dd>
              </div>
              <div>
                <dt className="muted">Last check-out</dt>
                <dd>
                  {formatInstant(
                    summary.check_in_summary.last_check_out_at,
                    timeZone,
                  )}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="muted mt-3">Check-in summary is not available yet.</p>
          )}
          {canViewCheckIns ? (
            <p className="mt-3">
              <Link href="/check-ins">Open front desk</Link>
            </p>
          ) : null}
        </section>
      </div>
    </div>
  );
}

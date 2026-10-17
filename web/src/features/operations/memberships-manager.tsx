"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
  listAllMembershipPlans,
  listMembers,
  listMembershipExpiry,
  listMemberships,
  operationMutation,
} from "@/features/operations/client";
import {
  updateMembership,
  updateMembershipPlan,
} from "@/features/operations/membership-writes";
import type {
  Member,
  Membership,
  MembershipCreateRequest,
  MembershipExpiryQuery,
  MembershipListPage,
  MembershipListQuery,
  MembershipPlan,
  MembershipPlanUpdate,
  MembershipUpdate,
} from "@/types/api";

const PAGE_LIMIT = 25;
const MEMBERSHIP_STATUSES = [
  "PENDING",
  "ACTIVE",
  "EXPIRED",
  "SUSPENDED",
  "CANCELLED",
] as const satisfies readonly NonNullable<MembershipListQuery["status"]>[];
const PAYMENT_STATUSES = [
  "UNPAID",
  "PARTIAL",
  "PAID",
  "PARTIALLY_REFUNDED",
  "REFUNDED",
] as const satisfies readonly NonNullable<
  MembershipListQuery["payment_status"]
>[];
const EXPIRY_BUCKETS = [
  "expired",
  "today",
  "within_3_days",
  "within_7_days",
  "within_30_days",
] as const satisfies readonly MembershipExpiryQuery["bucket"][];

type MembershipStatus = (typeof MEMBERSHIP_STATUSES)[number];
type PaymentStatus = (typeof PAYMENT_STATUSES)[number];
type ExpiryBucket = (typeof EXPIRY_BUCKETS)[number];
type ListMode = "all" | ExpiryBucket;

const EXPIRY_LABELS: Record<ExpiryBucket, string> = {
  expired: "Expired",
  today: "Expiring today",
  within_3_days: "Within 3 days",
  within_7_days: "Within 7 days",
  within_30_days: "Within 30 days",
};

type MembershipFilters = {
  listMode: ListMode;
  status: "" | MembershipStatus;
  paymentStatus: "" | PaymentStatus;
};

function isMembershipStatus(value: string): value is MembershipStatus {
  return (MEMBERSHIP_STATUSES as readonly string[]).includes(value);
}

function isPaymentStatus(value: string): value is PaymentStatus {
  return (PAYMENT_STATUSES as readonly string[]).includes(value);
}

function isExpiryBucket(value: string): value is ExpiryBucket {
  return (EXPIRY_BUCKETS as readonly string[]).includes(value);
}

function errorText(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function memberOptionLabel(member: Member): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return `${member.member_number} — ${name}`;
}

function memberTableLabel(
  member: Member | undefined,
  memberId: string,
): string {
  if (!member) return memberId;
  return `${member.first_name} ${member.last_name}`.trim() || memberId;
}

const DURATION_UNIT_LABELS: Record<MembershipPlan["duration_unit"], string> = {
  DAY: "Day",
  WEEK: "Week",
  MONTH: "Month",
  YEAR: "Year",
};

function planDurationLabel(row: MembershipPlan): string {
  const unit = DURATION_UNIT_LABELS[row.duration_unit];
  const suffix = row.duration_value === 1 ? "" : "s";
  return `${row.duration_value} ${unit}${suffix}`;
}

function planStatusLabel(row: MembershipPlan): string {
  return row.is_active ? "Active" : "Archived";
}

function planArchiveActionName(row: MembershipPlan): string {
  return `Archive ${row.name} (${row.code}) plan`;
}

function planEditActionName(row: MembershipPlan): string {
  return `Edit ${row.name} (${row.code}) plan`;
}

function membershipNoteActionName(
  row: Membership,
  memberLabel: string,
): string {
  return `Edit note for ${memberLabel}, ${row.plan_name}, ${row.id}`;
}

const PLAN_PRICE = /^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/;
const DURATION_UNITS = [
  "DAY",
  "WEEK",
  "MONTH",
  "YEAR",
] as const satisfies readonly MembershipPlan["duration_unit"][];
const MAX_DURATION = 2147483647;
const PLAN_CODE_MAX = 32;
const PLAN_NAME_MAX = 100;
const PLAN_TEXT_MAX = 4000;

type PlanDraft = {
  code: string;
  name: string;
  description: string;
  duration_value: string;
  duration_unit: MembershipPlan["duration_unit"];
  price: string;
};

type PlanEditorState = {
  source: MembershipPlan;
  draft: PlanDraft;
};

type NoteEditorState = {
  membershipId: string;
  memberLabel: string;
  planName: string;
  draft: string;
};

type Parsed<T> = { ok: true; body: T } | { ok: false; message: string };

function isDurationUnit(
  value: string,
): value is MembershipPlan["duration_unit"] {
  return (DURATION_UNITS as readonly string[]).includes(value);
}

function draftFromPlan(row: MembershipPlan): PlanDraft {
  return {
    code: row.code,
    name: row.name,
    description: row.description,
    duration_value: String(row.duration_value),
    duration_unit: row.duration_unit,
    price: row.price,
  };
}

function normalizePlanPrice(value: string): string | null {
  const trimmed = value.trim();
  if (!PLAN_PRICE.test(trimmed)) return null;
  const [whole, fraction = ""] = trimmed.split(".");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}

function planUpdateBody(draft: PlanDraft): Parsed<MembershipPlanUpdate> {
  const code = draft.code.trim();
  if (!code || /\s/u.test(code) || code.length > PLAN_CODE_MAX) {
    return { ok: false, message: "Enter a plan code without spaces." };
  }
  const name = draft.name.trim();
  if (!name || name.length > PLAN_NAME_MAX) {
    return { ok: false, message: "Enter a plan name." };
  }
  const description = draft.description.trim();
  if (description.length > PLAN_TEXT_MAX) {
    return {
      ok: false,
      message: "Enter a description up to 4000 characters.",
    };
  }
  if (!/^\d+$/.test(draft.duration_value.trim())) {
    return { ok: false, message: "Enter a whole duration of at least 1." };
  }
  const durationValue = Number(draft.duration_value.trim());
  if (durationValue < 1 || durationValue > MAX_DURATION) {
    return { ok: false, message: "Enter a whole duration of at least 1." };
  }
  if (!isDurationUnit(draft.duration_unit)) {
    return { ok: false, message: "Choose a duration unit." };
  }
  const price = normalizePlanPrice(draft.price);
  if (!price) {
    return {
      ok: false,
      message: "Enter a nonnegative price with up to 2 decimal places.",
    };
  }
  return {
    ok: true,
    body: {
      code,
      name,
      description,
      duration_value: durationValue,
      duration_unit: draft.duration_unit,
      price,
    },
  };
}

function membershipNoteBody(value: string): Parsed<MembershipUpdate> {
  const note = value.trim();
  if (note.length > PLAN_TEXT_MAX) {
    return { ok: false, message: "Enter a note up to 4000 characters." };
  }
  return { ok: true, body: { note } };
}

function uniqueById<T extends { id: string }>(rows: T[]): T[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    if (seen.has(row.id)) return false;
    seen.add(row.id);
    return true;
  });
}

function appendById<T extends { id: string }>(
  current: T[],
  incoming: T[],
): T[] {
  const seen = new Set(current.map((row) => row.id));
  const next = incoming.filter((row) => {
    if (seen.has(row.id)) return false;
    seen.add(row.id);
    return true;
  });
  return next.length === 0 ? current : [...current, ...next];
}

function requestMembershipPage(
  filters: MembershipFilters,
  after?: string,
): Promise<MembershipListPage> {
  if (filters.listMode !== "all") {
    const query: MembershipExpiryQuery = {
      bucket: filters.listMode,
      limit: PAGE_LIMIT,
    };
    if (after) query.after = after;
    return listMembershipExpiry(query);
  }
  const query: MembershipListQuery = { limit: PAGE_LIMIT };
  if (filters.status) query.status = filters.status;
  if (filters.paymentStatus) query.payment_status = filters.paymentStatus;
  if (after) query.after = after;
  return listMemberships(query);
}

function rememberMembers(
  current: ReadonlyMap<string, Member>,
  rows: readonly Member[],
): ReadonlyMap<string, Member> {
  let changed = false;
  const next = new Map(current);
  for (const member of rows) {
    next.set(member.id, member);
    changed = true;
  }
  return changed ? next : current;
}

export function MembershipsManager({
  canCreate,
  canUpdate,
  canCancel,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canCancel: boolean;
  currency: string;
  defaultDate: string;
}) {
  const [plans, setPlans] = useState<MembershipPlan[]>([]);
  const [planError, setPlanError] = useState("");
  const [planGeneration, setPlanGeneration] = useState(0);
  const [plansLoading, setPlansLoading] = useState(canCreate || canUpdate);
  const [lockedPlanIds, setLockedPlanIds] = useState<readonly string[]>([]);
  const [planEditor, setPlanEditor] = useState<PlanEditorState | null>(null);
  const [planEditError, setPlanEditError] = useState("");
  const [noteEditor, setNoteEditor] = useState<NoteEditorState | null>(null);
  const [noteError, setNoteError] = useState("");
  const [membershipBusyId, setMembershipBusyId] = useState<string | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState("");
  const [errorScope, setErrorScope] = useState<"list" | "page" | null>(null);
  const [pending, setPending] = useState(false);
  const [listMode, setListMode] = useState<ListMode>("all");
  const [status, setStatus] = useState<"" | MembershipStatus>("");
  const [paymentStatus, setPaymentStatus] = useState<"" | PaymentStatus>("");
  const [memberQuery, setMemberQuery] = useState("");
  const [memberResults, setMemberResults] = useState<Member[]>([]);
  const [memberSearchLoading, setMemberSearchLoading] = useState(false);
  const [memberSearchError, setMemberSearchError] = useState("");
  const [selectedMember, setSelectedMember] = useState<Member | null>(null);
  const [knownMembers, setKnownMembers] = useState<ReadonlyMap<string, Member>>(
    () => new Map(),
  );
  const requestVersion = useRef(0);
  const appliedCursors = useRef(new Set<string>());
  const planRequest = useRef(0);
  const planLocks = useRef(new Set<string>());
  const planVersions = useRef(new Map<string, number>());
  const memberSearchVersion = useRef(0);
  const listEpoch = useRef(0);
  const memberEpoch = useRef(0);
  const membershipBusy = useRef<string | null>(null);
  const pendingRef = useRef(false);
  const mountedRef = useRef(true);
  const canManagePlans = canCreate || canUpdate;
  const filters = { listMode, status, paymentStatus };
  const membershipActionsLocked = pending || membershipBusyId !== null;

  function publishPage(
    page: MembershipListPage,
    mode: "replace" | "append",
    cursor?: string,
  ) {
    setMemberships((current) =>
      mode === "replace"
        ? uniqueById(page.data)
        : appendById(current, page.data),
    );
    const next = page.meta.next_after;
    const stop =
      !page.meta.has_more ||
      !next ||
      next === cursor ||
      appliedCursors.current.has(next);
    setHasMore(!stop);
    setNextAfter(stop ? null : next);
    setError("");
    setErrorScope(null);
  }

  function resetMembershipPage() {
    listEpoch.current += 1;
    appliedCursors.current = new Set();
    requestVersion.current += 1;
    setMemberships([]);
    setNextAfter(null);
    setHasMore(false);
    setLoading(true);
    setPaging(false);
    setError("");
    setErrorScope(null);
  }

  async function refreshMemberships() {
    listEpoch.current += 1;
    const version = ++requestVersion.current;
    appliedCursors.current = new Set();
    setMemberships([]);
    setNextAfter(null);
    setHasMore(false);
    setLoading(true);
    setPaging(false);
    setError("");
    setErrorScope(null);
    try {
      const page = await requestMembershipPage(filters);
      if (version !== requestVersion.current) return;
      publishPage(page, "replace");
    } catch (caught) {
      if (version !== requestVersion.current) return;
      setMemberships([]);
      setNextAfter(null);
      setHasMore(false);
      setError(errorText(caught, "Unable to load memberships."));
      setErrorScope("list");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!canManagePlans) return;
    const version = ++planRequest.current;
    void listAllMembershipPlans()
      .then((rows) => {
        if (version !== planRequest.current) return;
        setPlans(rows);
        setPlanError("");
      })
      .catch((caught: unknown) => {
        if (version !== planRequest.current) return;
        setPlans([]);
        setPlanError(errorText(caught, "Unable to load membership plans."));
      })
      .finally(() => {
        if (version === planRequest.current) setPlansLoading(false);
      });
    return () => {
      planRequest.current += 1;
    };
  }, [canManagePlans, planGeneration]);

  useEffect(() => {
    const version = ++requestVersion.current;
    const currentFilters = { listMode, status, paymentStatus };
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const page = await requestMembershipPage(currentFilters);
          if (version !== requestVersion.current) return;
          setMemberships(uniqueById(page.data));
          const next = page.meta.next_after;
          const stop =
            !page.meta.has_more || !next || appliedCursors.current.has(next);
          setHasMore(!stop);
          setNextAfter(stop ? null : next);
          setError("");
          setErrorScope(null);
        } catch (caught) {
          if (version !== requestVersion.current) return;
          setMemberships([]);
          setNextAfter(null);
          setHasMore(false);
          setError(errorText(caught, "Unable to load memberships."));
          setErrorScope("list");
        } finally {
          if (version === requestVersion.current) setLoading(false);
        }
      })();
    }, 250);
    return () => {
      window.clearTimeout(timer);
      requestVersion.current += 1;
      listEpoch.current += 1;
    };
  }, [listMode, paymentStatus, status]);

  useEffect(() => {
    const trimmed = memberQuery.trim();
    if (!trimmed) return;
    const version = ++memberSearchVersion.current;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const page = await listMembers({
            search: trimmed,
            status: "ACTIVE",
            limit: 25,
          });
          if (version !== memberSearchVersion.current) return;
          setMemberResults(page.data);
          setKnownMembers((current) => rememberMembers(current, page.data));
          setMemberSearchError("");
        } catch (caught) {
          if (version !== memberSearchVersion.current) return;
          setMemberResults([]);
          setMemberSearchError(errorText(caught, "Unable to search members."));
        } finally {
          if (version === memberSearchVersion.current) {
            setMemberSearchLoading(false);
          }
        }
      })();
    }, 250);
    return () => {
      window.clearTimeout(timer);
      memberSearchVersion.current += 1;
    };
  }, [memberQuery]);

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
      const page = await requestMembershipPage(filters, cursor);
      if (version !== requestVersion.current) return;
      publishPage(page, "append", cursor);
    } catch (caught) {
      appliedCursors.current.delete(cursor);
      if (version === requestVersion.current) {
        setError(errorText(caught, "Unable to load memberships."));
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
    void refreshMemberships();
  }

  function retryPlanCatalog() {
    setPlans([]);
    setPlanError("");
    setPlansLoading(true);
    setPlanGeneration((current) => current + 1);
  }

  function releasePlan(planId: string) {
    planLocks.current.delete(planId);
    if (mountedRef.current) setLockedPlanIds([...planLocks.current]);
  }

  function openPlanEditor(target: MembershipPlan) {
    if (!canUpdate || planLocks.current.has(target.id)) return;
    if (planEditor?.source.id === target.id) return;
    if (planEditor) {
      const next = (planVersions.current.get(planEditor.source.id) ?? 0) + 1;
      planVersions.current.set(planEditor.source.id, next);
    }
    setPlanEditor({ source: target, draft: draftFromPlan(target) });
    setPlanEditError("");
  }

  function cancelPlanEdit() {
    if (!planEditor || planLocks.current.has(planEditor.source.id)) return;
    const next = (planVersions.current.get(planEditor.source.id) ?? 0) + 1;
    planVersions.current.set(planEditor.source.id, next);
    setPlanEditor(null);
    setPlanEditError("");
  }

  async function savePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!planEditor) return;
    const target = planEditor.source;
    if (planLocks.current.has(target.id)) return;
    const parsed = planUpdateBody(planEditor.draft);
    if (!parsed.ok) {
      setPlanEditError(parsed.message);
      return;
    }
    planLocks.current.add(target.id);
    setLockedPlanIds([...planLocks.current]);
    const version = planVersions.current.get(target.id) ?? 0;
    setPlanEditError("");
    try {
      const updated = await updateMembershipPlan(target.id, parsed.body);
      if (!mountedRef.current) return;
      if ((planVersions.current.get(target.id) ?? 0) !== version) return;
      setPlans((current) =>
        current.map((row) => (row.id === updated.id ? updated : row)),
      );
      setPlanEditor((current) =>
        current?.source.id === target.id ? null : current,
      );
    } catch (caught) {
      if (!mountedRef.current) return;
      if ((planVersions.current.get(target.id) ?? 0) !== version) return;
      setPlanEditError(errorText(caught, "Unable to update plan."));
    } finally {
      releasePlan(target.id);
    }
  }

  async function archivePlan(target: MembershipPlan) {
    if (
      !canUpdate ||
      !target.is_active ||
      planLocks.current.has(target.id) ||
      planEditor?.source.id === target.id
    ) {
      return;
    }
    planLocks.current.add(target.id);
    setLockedPlanIds([...planLocks.current]);
    if (!window.confirm(`Archive plan ${target.name} (${target.code})?`)) {
      releasePlan(target.id);
      return;
    }
    const version = planVersions.current.get(target.id) ?? 0;
    setError("");
    setErrorScope(null);
    try {
      const archived = await operationMutation<MembershipPlan>(
        `membership-plans/${target.id}/archive/`,
        {},
      );
      if (!mountedRef.current) return;
      if ((planVersions.current.get(target.id) ?? 0) !== version) return;
      setPlans((current) =>
        current.map((row) => (row.id === target.id ? archived : row)),
      );
    } catch (caught) {
      if (!mountedRef.current) return;
      if ((planVersions.current.get(target.id) ?? 0) !== version) return;
      setError(errorText(caught, "Unable to archive plan."));
      setErrorScope(null);
    } finally {
      releasePlan(target.id);
    }
  }

  function selectMember(member: Member) {
    if (selectedMember?.id !== member.id) memberEpoch.current += 1;
    setSelectedMember(member);
    setKnownMembers((current) => rememberMembers(current, [member]));
  }

  function beginSharedPending(): boolean {
    if (pendingRef.current || membershipBusy.current !== null) return false;
    pendingRef.current = true;
    setPending(true);
    return true;
  }

  function endSharedPending() {
    pendingRef.current = false;
    membershipBusy.current = null;
    if (!mountedRef.current) return;
    setPending(false);
    setMembershipBusyId(null);
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!beginSharedPending()) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await operationMutation<MembershipPlan>("membership-plans/", {
        code: form.get("code"),
        name: form.get("name"),
        duration_value: Number(form.get("duration_value")),
        duration_unit: form.get("duration_unit"),
        price: form.get("price"),
      });
      formElement.reset();
      setPlanGeneration((current) => current + 1);
      await refreshMemberships();
    } catch (caught) {
      if (!mountedRef.current) return;
      setError(errorText(caught, "Unable to create plan."));
      setErrorScope(null);
    } finally {
      endSharedPending();
    }
  }

  async function createMembership(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedMember) {
      setError("Select a member.");
      setErrorScope(null);
      return;
    }
    if (!beginSharedPending()) return;
    const form = new FormData(event.currentTarget);
    const body = {
      member_id: selectedMember.id,
      plan_id: String(form.get("plan_id") ?? ""),
      start_date: String(form.get("start_date") ?? ""),
      discount_amount: String(form.get("discount_amount") || "0.00"),
      note: String(form.get("note") ?? "").trim(),
    } satisfies MembershipCreateRequest;
    try {
      await operationMutation<Membership>("memberships/", body);
      await refreshMemberships();
    } catch (caught) {
      if (!mountedRef.current) return;
      setError(errorText(caught, "Unable to create membership."));
      setErrorScope(null);
    } finally {
      endSharedPending();
    }
  }

  function openNoteEditor(membership: Membership) {
    if (!canUpdate || pendingRef.current || membershipBusy.current !== null) {
      return;
    }
    setNoteEditor({
      membershipId: membership.id,
      memberLabel: memberTableLabel(
        knownMembers.get(membership.member_id),
        membership.member_id,
      ),
      planName: membership.plan_name,
      draft: membership.note,
    });
    setNoteError("");
  }

  function cancelNoteEdit() {
    if (membershipBusy.current !== null) return;
    setNoteEditor(null);
    setNoteError("");
  }

  function noteResultIsCurrent(epoch: number, membersAtStart: number): boolean {
    return (
      mountedRef.current &&
      epoch === listEpoch.current &&
      membersAtStart === memberEpoch.current
    );
  }

  async function saveNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!noteEditor) return;
    const editor = noteEditor;
    const parsed = membershipNoteBody(editor.draft);
    if (!parsed.ok) {
      setNoteError(parsed.message);
      return;
    }
    if (!beginSharedPending()) return;
    membershipBusy.current = editor.membershipId;
    setMembershipBusyId(editor.membershipId);
    const epoch = listEpoch.current;
    const membersAtStart = memberEpoch.current;
    setNoteError("");
    try {
      const updated = await updateMembership(editor.membershipId, parsed.body);
      if (!noteResultIsCurrent(epoch, membersAtStart)) return;
      setMemberships((current) =>
        current.map((row) => (row.id === updated.id ? updated : row)),
      );
      setNoteEditor((current) =>
        current?.membershipId === editor.membershipId ? null : current,
      );
    } catch (caught) {
      if (!noteResultIsCurrent(epoch, membersAtStart)) return;
      setNoteError(errorText(caught, "Unable to update membership note."));
    } finally {
      endSharedPending();
    }
  }

  async function membershipAction(
    membership: Membership,
    action: "suspend" | "resume" | "cancel",
  ) {
    if (pendingRef.current || membershipBusy.current !== null) return;
    const body: { reason?: string } = {};
    if (action === "cancel") {
      const reason = window.prompt("Cancellation reason");
      if (reason === null || reason.trim() === "") return;
      if (pendingRef.current || membershipBusy.current !== null) return;
      body.reason = reason.trim();
    }
    if (!beginSharedPending()) return;
    membershipBusy.current = membership.id;
    setMembershipBusyId(membership.id);
    const epoch = listEpoch.current;
    const membersAtStart = memberEpoch.current;
    let succeeded = false;
    try {
      await operationMutation(`memberships/${membership.id}/${action}/`, body);
      if (!noteResultIsCurrent(epoch, membersAtStart)) return;
      succeeded = true;
    } catch (caught) {
      if (!noteResultIsCurrent(epoch, membersAtStart)) return;
      setError(errorText(caught, "Unable to update membership."));
      setErrorScope(null);
    } finally {
      endSharedPending();
    }
    if (succeeded && mountedRef.current) await refreshMemberships();
  }

  const activePlans = planError ? [] : plans.filter((plan) => plan.is_active);
  const showPlanTable = !planError && plans.length > 0;
  const trimmedMemberQuery = memberQuery.trim();

  return (
    <div className="grid gap-6">
      {error ? (
        <div className="grid gap-3">
          <p className="error" role="alert">
            {error}
          </p>
          {errorScope ? (
            <button className="button" onClick={retry} type="button">
              {errorScope === "page" ? "Retry loading memberships" : "Retry"}
            </button>
          ) : null}
        </div>
      ) : null}
      {canManagePlans ? (
        <section
          aria-busy={plansLoading}
          aria-labelledby="membership-plans-title"
          className="card overflow-hidden"
        >
          <div className="border-b border-slate-200 p-4">
            <h2 className="text-lg font-bold" id="membership-plans-title">
              Membership plans
            </h2>
          </div>
          {plansLoading && !planError && plans.length === 0 ? (
            <p className="muted m-4" role="status">
              Loading membership plans.
            </p>
          ) : null}
          {planError ? (
            <div className="grid gap-3 p-4">
              <p className="error" role="alert">
                {planError}
              </p>
              <button
                className="button"
                onClick={retryPlanCatalog}
                type="button"
              >
                Retry loading plans
              </button>
            </div>
          ) : null}
          {!plansLoading && !planError && plans.length === 0 ? (
            <p className="muted m-4" role="status">
              No membership plans.
            </p>
          ) : null}
          {showPlanTable ? (
            <div className="overflow-x-auto">
              <table
                aria-label="Membership plans"
                className="w-full text-left text-sm"
              >
                <thead className="bg-slate-50">
                  <tr>
                    <th className="p-3" scope="col">
                      Code
                    </th>
                    <th className="p-3" scope="col">
                      Name
                    </th>
                    <th className="p-3" scope="col">
                      Duration
                    </th>
                    <th className="p-3" scope="col">
                      Price
                    </th>
                    <th className="p-3" scope="col">
                      Status
                    </th>
                    {canUpdate ? (
                      <th className="p-3" scope="col">
                        Action
                      </th>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {plans.map((plan) => (
                    <tr className="border-t border-slate-100" key={plan.id}>
                      <td className="p-3">{plan.code}</td>
                      <td className="p-3">{plan.name}</td>
                      <td className="p-3">{planDurationLabel(plan)}</td>
                      <td className="p-3">
                        {plan.price} {plan.currency}
                      </td>
                      <td className="p-3">{planStatusLabel(plan)}</td>
                      {canUpdate ? (
                        <td className="p-3">
                          <div className="flex flex-wrap gap-3">
                            <button
                              aria-label={planEditActionName(plan)}
                              className="underline"
                              disabled={lockedPlanIds.includes(plan.id)}
                              onClick={() => openPlanEditor(plan)}
                              type="button"
                            >
                              Edit {plan.name} plan
                            </button>
                            {plan.is_active ? (
                              <button
                                aria-label={planArchiveActionName(plan)}
                                className="underline"
                                disabled={
                                  lockedPlanIds.includes(plan.id) ||
                                  planEditor?.source.id === plan.id
                                }
                                onClick={() => void archivePlan(plan)}
                                type="button"
                              >
                                Archive {plan.name} plan
                              </button>
                            ) : null}
                          </div>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      {canCreate && (
        <div className="grid gap-6 xl:grid-cols-2">
          <form className="card grid gap-4 p-5" onSubmit={createPlan}>
            <h2 className="text-lg font-bold">New plan</h2>
            <div className="grid grid-cols-2 gap-3">
              <label className="field">
                Code
                <input className="input" name="code" required />
              </label>
              <label className="field">
                Name
                <input className="input" name="name" required />
              </label>
              <label className="field">
                Duration
                <input
                  className="input"
                  min="1"
                  name="duration_value"
                  required
                  type="number"
                />
              </label>
              <label className="field">
                Unit
                <select className="input" name="duration_unit">
                  <option value="MONTH">Month</option>
                  <option value="DAY">Day</option>
                  <option value="WEEK">Week</option>
                  <option value="YEAR">Year</option>
                </select>
              </label>
              <label className="field">
                Price
                <input
                  className="input"
                  min="0"
                  name="price"
                  required
                  step="0.01"
                  type="number"
                />
              </label>
              <label className="field">
                Currency
                <input
                  className="input"
                  defaultValue={currency}
                  maxLength={3}
                  readOnly
                />
              </label>
            </div>
            <button className="button" disabled={pending} type="submit">
              Save plan
            </button>
          </form>
          <form className="card grid gap-4 p-5" onSubmit={createMembership}>
            <h2 className="text-lg font-bold">Start membership</h2>
            <fieldset className="grid gap-3">
              <legend className="font-semibold">Member</legend>
              <label className="field">
                Search members
                <input
                  aria-label="Search members"
                  className="input"
                  maxLength={100}
                  onChange={(event) => {
                    const value = event.target.value;
                    setMemberQuery(value);
                    memberSearchVersion.current += 1;
                    if (!value.trim()) {
                      setMemberResults([]);
                      setMemberSearchLoading(false);
                      setMemberSearchError("");
                      return;
                    }
                    setMemberSearchLoading(true);
                    setMemberSearchError("");
                  }}
                  placeholder="Name, number, phone, or email"
                  value={memberQuery}
                />
              </label>
              {memberSearchLoading ? (
                <p className="muted" role="status">
                  Searching members.
                </p>
              ) : null}
              {memberSearchError ? (
                <p className="error" role="alert">
                  {memberSearchError}
                </p>
              ) : null}
              {!memberSearchLoading &&
              trimmedMemberQuery &&
              memberResults.length === 0 &&
              !memberSearchError ? (
                <p className="muted">No matching members.</p>
              ) : null}
              {memberResults.length > 0 ? (
                <ul aria-label="Matching members" className="grid gap-2">
                  {memberResults.map((member) => (
                    <li key={member.id}>
                      <button
                        aria-pressed={selectedMember?.id === member.id}
                        className="underline"
                        onClick={() => selectMember(member)}
                        type="button"
                      >
                        {memberOptionLabel(member)}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              <p>
                {selectedMember
                  ? `Selected member: ${memberOptionLabel(selectedMember)}`
                  : "No member selected."}
              </p>
            </fieldset>
            <label className="field">
              Plan
              <select
                className="input"
                disabled={Boolean(planError)}
                name="plan_id"
                required
              >
                <option value="">Select plan</option>
                {activePlans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name} — {plan.price} {plan.currency}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Start date
              <input
                className="input"
                defaultValue={defaultDate}
                name="start_date"
                required
                type="date"
              />
            </label>
            <label className="field">
              Discount
              <input
                className="input"
                defaultValue="0.00"
                min="0"
                name="discount_amount"
                step="0.01"
                type="number"
              />
            </label>
            <label className="field">
              Note
              <textarea
                className="input min-h-24"
                maxLength={4000}
                name="note"
              />
            </label>
            <button className="button" disabled={pending} type="submit">
              Start membership
            </button>
          </form>
        </div>
      )}
      <section aria-busy={loading || paging} className="card overflow-hidden">
        <div className="flex flex-wrap gap-3 border-b border-slate-200 p-4">
          <select
            aria-label="Membership list"
            className="input max-w-56"
            onChange={(event) => {
              const value = event.target.value;
              setListMode(isExpiryBucket(value) ? value : "all");
              resetMembershipPage();
            }}
            value={listMode}
          >
            <option value="all">All memberships</option>
            {EXPIRY_BUCKETS.map((bucket) => (
              <option key={bucket} value={bucket}>
                {EXPIRY_LABELS[bucket]}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by status"
            className="input max-w-48"
            disabled={listMode !== "all"}
            onChange={(event) => {
              const value = event.target.value;
              setStatus(isMembershipStatus(value) ? value : "");
              resetMembershipPage();
            }}
            value={status}
          >
            <option value="">All statuses</option>
            <option value="PENDING">Pending</option>
            <option value="ACTIVE">Active</option>
            <option value="EXPIRED">Expired</option>
            <option value="SUSPENDED">Suspended</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
          <select
            aria-label="Filter by payment status"
            className="input max-w-56"
            disabled={listMode !== "all"}
            onChange={(event) => {
              const value = event.target.value;
              setPaymentStatus(isPaymentStatus(value) ? value : "");
              resetMembershipPage();
            }}
            value={paymentStatus}
          >
            <option value="">All payment statuses</option>
            <option value="UNPAID">Unpaid</option>
            <option value="PARTIAL">Partial</option>
            <option value="PAID">Paid</option>
            <option value="PARTIALLY_REFUNDED">Partially refunded</option>
            <option value="REFUNDED">Refunded</option>
          </select>
        </div>
        {loading ? (
          <p className="muted m-4" role="status">
            Loading memberships.
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table aria-label="Memberships" className="w-full text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3" scope="col">
                  Member
                </th>
                <th className="p-3" scope="col">
                  Plan
                </th>
                <th className="p-3" scope="col">
                  Dates
                </th>
                <th className="p-3" scope="col">
                  Total
                </th>
                <th className="p-3" scope="col">
                  Status
                </th>
                <th className="p-3" scope="col">
                  Note
                </th>
                <th className="p-3" scope="col">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {memberships.map((membership) => (
                <tr className="border-t border-slate-100" key={membership.id}>
                  <td className="p-3">
                    {memberTableLabel(
                      knownMembers.get(membership.member_id),
                      membership.member_id,
                    )}
                  </td>
                  <td className="p-3">{membership.plan_name}</td>
                  <td className="p-3">
                    {membership.start_date} – {membership.expiry_date}
                  </td>
                  <td className="p-3">
                    {membership.final_amount} {membership.currency}
                  </td>
                  <td className="p-3">
                    {membership.status} / {membership.payment_status}
                  </td>
                  <td className="p-3">
                    {membership.note ? membership.note : "No note"}
                  </td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-3">
                      {canUpdate ? (
                        <button
                          aria-label={membershipNoteActionName(
                            membership,
                            memberTableLabel(
                              knownMembers.get(membership.member_id),
                              membership.member_id,
                            ),
                          )}
                          className="underline"
                          disabled={membershipActionsLocked}
                          onClick={() => openNoteEditor(membership)}
                          type="button"
                        >
                          Edit note
                        </button>
                      ) : null}
                      {canUpdate && membership.status === "ACTIVE" && (
                        <button
                          className="underline"
                          disabled={membershipActionsLocked}
                          onClick={() =>
                            void membershipAction(membership, "suspend")
                          }
                          type="button"
                        >
                          Suspend
                        </button>
                      )}
                      {canUpdate && membership.status === "SUSPENDED" && (
                        <button
                          className="underline"
                          disabled={membershipActionsLocked}
                          onClick={() =>
                            void membershipAction(membership, "resume")
                          }
                          type="button"
                        >
                          Resume
                        </button>
                      )}
                      {canCancel &&
                        ["PENDING", "ACTIVE", "SUSPENDED"].includes(
                          membership.status,
                        ) && (
                          <button
                            className="underline"
                            disabled={membershipActionsLocked}
                            onClick={() =>
                              void membershipAction(membership, "cancel")
                            }
                            type="button"
                          >
                            Cancel
                          </button>
                        )}
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && !error && memberships.length === 0 ? (
                <tr>
                  <td className="muted p-6 text-center" colSpan={7}>
                    No memberships found.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {paging ? (
          <p className="muted m-4" role="status">
            Loading more memberships.
          </p>
        ) : null}
        {hasMore && nextAfter ? (
          <div className="p-4">
            <button
              className="button"
              disabled={paging}
              onClick={() => void loadMore()}
              type="button"
            >
              Load more memberships
            </button>
          </div>
        ) : null}
      </section>
      {planEditor ? (
        <PlanEditorDialog
          error={planEditError}
          pending={lockedPlanIds.includes(planEditor.source.id)}
          state={planEditor}
          onCancel={cancelPlanEdit}
          onDraft={(draft) =>
            setPlanEditor((current) =>
              current ? { ...current, draft } : current,
            )
          }
          onSubmit={(event) => void savePlan(event)}
        />
      ) : null}
      {noteEditor ? (
        <NoteEditorDialog
          error={noteError}
          pending={membershipBusyId === noteEditor.membershipId}
          state={noteEditor}
          onCancel={cancelNoteEdit}
          onDraft={(draft) =>
            setNoteEditor((current) =>
              current ? { ...current, draft } : current,
            )
          }
          onSubmit={(event) => void saveNote(event)}
        />
      ) : null}
    </div>
  );
}

function PlanEditorDialog({
  state,
  error,
  pending,
  onDraft,
  onSubmit,
  onCancel,
}: {
  state: PlanEditorState;
  error: string;
  pending: boolean;
  onDraft: (draft: PlanDraft) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const { source, draft } = state;
  const title = `Edit ${source.name} (${source.code})`;
  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-slate-950/40 p-4">
      <div
        aria-busy={pending}
        aria-labelledby={titleId}
        aria-modal="true"
        className="card grid max-h-[90vh] w-full max-w-lg gap-4 overflow-y-auto p-5"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !pending) onCancel();
        }}
      >
        <h2 className="text-lg font-bold" id={titleId}>
          {title}
        </h2>
        <form className="grid gap-4" onSubmit={onSubmit}>
          <div className="grid grid-cols-2 gap-3">
            <label className="field">
              Code
              <input
                className="input"
                maxLength={PLAN_CODE_MAX}
                onChange={(event) =>
                  onDraft({ ...draft, code: event.target.value })
                }
                required
                value={draft.code}
              />
            </label>
            <label className="field">
              Name
              <input
                className="input"
                maxLength={PLAN_NAME_MAX}
                onChange={(event) =>
                  onDraft({ ...draft, name: event.target.value })
                }
                required
                value={draft.name}
              />
            </label>
            <label className="field col-span-2">
              Description
              <textarea
                className="input min-h-24"
                maxLength={PLAN_TEXT_MAX}
                onChange={(event) =>
                  onDraft({ ...draft, description: event.target.value })
                }
                value={draft.description}
              />
            </label>
            <label className="field">
              Duration
              <input
                className="input"
                inputMode="numeric"
                min="1"
                onChange={(event) =>
                  onDraft({ ...draft, duration_value: event.target.value })
                }
                required
                value={draft.duration_value}
              />
            </label>
            <label className="field">
              Unit
              <select
                className="input"
                onChange={(event) => {
                  const value = event.target.value;
                  if (!isDurationUnit(value)) return;
                  onDraft({ ...draft, duration_unit: value });
                }}
                value={draft.duration_unit}
              >
                {DURATION_UNITS.map((unit) => (
                  <option key={unit} value={unit}>
                    {DURATION_UNIT_LABELS[unit]}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Price
              <input
                className="input"
                inputMode="decimal"
                onChange={(event) =>
                  onDraft({ ...draft, price: event.target.value })
                }
                required
                value={draft.price}
              />
            </label>
            <label className="field">
              Currency
              <input className="input" readOnly value={source.currency} />
            </label>
          </div>
          <p>
            Status <span>{planStatusLabel(source)}</span>
          </p>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button className="button" disabled={pending} type="submit">
              {`Save ${source.name} (${source.code}) plan`}
            </button>
            <button
              className="button bg-white text-slate-900 ring-1 ring-slate-300"
              disabled={pending}
              onClick={onCancel}
              type="button"
            >
              {`Cancel editing ${source.name} (${source.code}) plan`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function NoteEditorDialog({
  state,
  error,
  pending,
  onDraft,
  onSubmit,
  onCancel,
}: {
  state: NoteEditorState;
  error: string;
  pending: boolean;
  onDraft: (draft: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const identity = `${state.memberLabel}, ${state.planName}, ${state.membershipId}`;
  const title = `Edit note for ${identity}`;
  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-slate-950/40 p-4">
      <div
        aria-busy={pending}
        aria-labelledby={titleId}
        aria-modal="true"
        className="card grid w-full max-w-lg gap-4 p-5"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !pending) onCancel();
        }}
      >
        <h2 className="text-lg font-bold" id={titleId}>
          {title}
        </h2>
        <form className="grid gap-4" onSubmit={onSubmit}>
          <label className="field">
            Membership note
            <textarea
              className="input min-h-28"
              maxLength={PLAN_TEXT_MAX}
              onChange={(event) => onDraft(event.target.value)}
              value={state.draft}
            />
          </label>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button className="button" disabled={pending} type="submit">
              {`Save note for ${identity}`}
            </button>
            <button
              className="button bg-white text-slate-900 ring-1 ring-slate-300"
              disabled={pending}
              onClick={onCancel}
              type="button"
            >
              {`Discard note edit for ${identity}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

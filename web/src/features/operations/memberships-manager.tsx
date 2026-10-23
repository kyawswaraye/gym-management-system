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
import { actionButtonClass } from "@/lib/ui/action-button";
import { compactIdentifier, IdentifierValue } from "@/lib/ui/identifier";
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
  return `Edit note for ${compactIdentifier(memberLabel)} · ${row.plan_name}`;
}

function rowActionClass(
  tone: "edit" | "archive" | "cancel" | "warn" | "success",
): string {
  return `button button-sm button-${tone}`;
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
  const [archiveTarget, setArchiveTarget] = useState<MembershipPlan | null>(
    null,
  );
  const [cancelTarget, setCancelTarget] = useState<Membership | null>(null);
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
  const [showPlanForm, setShowPlanForm] = useState(false);
  const [showMembershipForm, setShowMembershipForm] = useState(false);
  const [draftPlanId, setDraftPlanId] = useState("");
  const [draftStartDate, setDraftStartDate] = useState(defaultDate);
  const [draftDiscount, setDraftDiscount] = useState("0.00");
  const [draftNote, setDraftNote] = useState("");
  const [membershipFormError, setMembershipFormError] = useState("");
  const [showPlans, setShowPlans] = useState(false);
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
  const memberSearchRef = useRef<HTMLInputElement>(null);
  const startMembershipButtonRef = useRef<HTMLButtonElement>(null);
  const startMembershipWasOpen = useRef(false);
  const newPlanTitleId = useId();
  const startMembershipTitleId = useId();
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

  useEffect(() => {
    if (showMembershipForm) {
      startMembershipWasOpen.current = true;
      memberSearchRef.current?.focus();
      return;
    }
    if (!startMembershipWasOpen.current) return;
    startMembershipWasOpen.current = false;
    startMembershipButtonRef.current?.focus();
  }, [showMembershipForm]);

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
    setArchiveTarget(null);
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
    setMembershipFormError("");
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
      setShowPlanForm(false);
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
      setMembershipFormError("Select a member.");
      return;
    }
    if (!beginSharedPending()) return;
    setMembershipFormError("");
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
      setShowMembershipForm(false);
      await refreshMemberships();
    } catch (caught) {
      if (!mountedRef.current) return;
      setMembershipFormError(errorText(caught, "Unable to create membership."));
    } finally {
      endSharedPending();
    }
  }

  function closeStartMembershipForm() {
    if (pendingRef.current) return;
    setMembershipFormError("");
    setShowMembershipForm(false);
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
    reason?: string,
  ) {
    if (pendingRef.current || membershipBusy.current !== null) return;
    const body: { reason?: string } = {};
    if (action === "cancel") {
      const trimmed = reason?.trim() ?? "";
      if (!trimmed) return;
      body.reason = trimmed;
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
    <div className="grid gap-5">
      {error ? (
        <div className="order-1 grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
          <p className="text-sm font-medium text-red-800" role="alert">
            {error}
          </p>
          {errorScope ? (
            <button className="button w-fit" onClick={retry} type="button">
              {errorScope === "page" ? "Retry loading memberships" : "Retry"}
            </button>
          ) : null}
        </div>
      ) : null}
      {canCreate || canManagePlans ? (
        <section className="order-2 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs font-bold tracking-[0.16em] text-amber-600 uppercase">
                Quick actions
              </p>
              <h2 className="mt-1 text-lg font-bold text-slate-900">
                What would you like to do?
              </h2>
              <p className="mt-0.5 text-sm text-slate-500">
                Start a membership or manage the plans available to members.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {canCreate ? (
                <button
                  aria-expanded={showPlanForm}
                  className={actionButtonClass(
                    showPlanForm ? "Close plan form" : "New plan",
                  )}
                  onClick={() => setShowPlanForm((current) => !current)}
                  type="button"
                >
                  {showPlanForm ? "Close plan form" : "New plan"}
                </button>
              ) : null}
              {canManagePlans ? (
                <button
                  aria-expanded={showPlans}
                  className={actionButtonClass(
                    showPlans ? "Hide plans" : "View plans",
                  )}
                  onClick={() => setShowPlans((current) => !current)}
                  type="button"
                >
                  {showPlans ? "Hide plans" : "View plans"}
                </button>
              ) : null}
              {canCreate ? (
                <button
                  ref={startMembershipButtonRef}
                  aria-label={
                    showMembershipForm
                      ? "Close start membership form"
                      : "Open start membership form"
                  }
                  aria-expanded={showMembershipForm}
                  className={actionButtonClass(
                    showMembershipForm
                      ? "Close membership form"
                      : "Start membership",
                  )}
                  onClick={() => {
                    if (showMembershipForm) {
                      closeStartMembershipForm();
                      return;
                    }
                    setShowMembershipForm(true);
                  }}
                  type="button"
                >
                  {showMembershipForm
                    ? "Close membership form"
                    : "Start membership"}
                </button>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
      {canManagePlans ? (
        <section
          aria-busy={plansLoading}
          aria-labelledby="membership-plans-title"
          className={
            showPlans
              ? "order-3 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
              : "hidden"
          }
        >
          <div className="border-b border-slate-200 px-5 py-4">
            <h2
              className="text-lg font-bold text-slate-900"
              id="membership-plans-title"
            >
              Membership plans
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Available pricing and duration options.
            </p>
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
                <thead className="bg-slate-800 text-white">
                  <tr>
                    <th className="px-5 py-3 text-xs font-semibold" scope="col">
                      Code
                    </th>
                    <th className="px-5 py-3 text-xs font-semibold" scope="col">
                      Name
                    </th>
                    <th className="px-5 py-3 text-xs font-semibold" scope="col">
                      Duration
                    </th>
                    <th className="px-5 py-3 text-xs font-semibold" scope="col">
                      Price
                    </th>
                    <th className="px-5 py-3 text-xs font-semibold" scope="col">
                      Status
                    </th>
                    {canUpdate ? (
                      <th
                        className="px-5 py-3 text-xs font-semibold"
                        scope="col"
                      >
                        Action
                      </th>
                    ) : null}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {plans.map((plan) => (
                    <tr className="hover:bg-slate-50" key={plan.id}>
                      <td className="px-5 py-4 font-mono text-xs font-bold text-slate-600">
                        {plan.code}
                      </td>
                      <td className="px-5 py-4 font-semibold text-slate-900">
                        {plan.name}
                      </td>
                      <td className="px-5 py-4 text-slate-600">
                        {planDurationLabel(plan)}
                      </td>
                      <td className="whitespace-nowrap px-5 py-4 font-semibold text-slate-900">
                        {plan.price} {plan.currency}
                      </td>
                      <td className="px-5 py-4">
                        <StatusBadge
                          label={planStatusLabel(plan)}
                          status={planStatusLabel(plan).toUpperCase()}
                        />
                      </td>
                      {canUpdate ? (
                        <td className="px-5 py-4">
                          <div className="flex flex-nowrap gap-2">
                            <button
                              aria-label={planEditActionName(plan)}
                              className={rowActionClass("edit")}
                              disabled={lockedPlanIds.includes(plan.id)}
                              onClick={() => openPlanEditor(plan)}
                              type="button"
                            >
                              Edit
                            </button>
                            {plan.is_active ? (
                              <button
                                aria-label={planArchiveActionName(plan)}
                                className={rowActionClass("archive")}
                                disabled={
                                  lockedPlanIds.includes(plan.id) ||
                                  planEditor?.source.id === plan.id
                                }
                                onClick={() => setArchiveTarget(plan)}
                                type="button"
                              >
                                Archive
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
        <div className="order-4">
          {showPlanForm ? (
            <div
              className="fixed inset-0 z-50 grid place-items-center overflow-y-auto p-4 py-8"
              role="presentation"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget && !pending) {
                  setShowPlanForm(false);
                }
              }}
            >
              <form
                aria-labelledby={newPlanTitleId}
                aria-modal="true"
                className="grid max-h-[90vh] w-full max-w-xl gap-5 overflow-y-auto rounded-2xl bg-white p-5 sm:p-6"
                onSubmit={createPlan}
                role="dialog"
                onKeyDown={(event) => {
                  if (event.key === "Escape" && !pending) {
                    setShowPlanForm(false);
                  }
                }}
              >
                <div className="flex items-start gap-3 border-b border-slate-100 pb-4">
                  <span
                    className="grid min-h-9 shrink-0 place-items-center rounded-lg bg-slate-100 px-2 text-[0.65rem] font-black tracking-wide text-slate-700"
                    aria-hidden="true"
                  >
                    PLAN
                  </span>
                  <div>
                    <h2
                      className="text-xl font-bold text-slate-900"
                      id={newPlanTitleId}
                    >
                      New plan
                    </h2>
                    <p className="mt-1 text-sm text-slate-500">
                      Define the price and membership duration.
                    </p>
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2 [&_.field]:text-sm [&_.field]:font-semibold [&_.field]:text-slate-700">
                  <label className="field sm:col-span-2">
                    Code
                    <input
                      className="input"
                      name="code"
                      placeholder="e.g. MONTHLY"
                      required
                    />
                  </label>
                  <label className="field sm:col-span-2">
                    Name
                    <input
                      className="input"
                      name="name"
                      placeholder="e.g. Monthly membership"
                      required
                    />
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
                      placeholder="0.00"
                      required
                      step="0.01"
                      type="number"
                    />
                  </label>
                  <label className="field">
                    Currency
                    <input
                      className="input bg-slate-50 text-slate-500"
                      defaultValue={currency}
                      maxLength={3}
                      readOnly
                    />
                  </label>
                </div>
                <div className="flex justify-end border-t border-slate-100 pt-4">
                  <button
                    className="button button-cancel"
                    disabled={pending}
                    onClick={() => setShowPlanForm(false)}
                    type="button"
                  >
                    Cancel
                  </button>
                  <button
                    className="button button-edit"
                    disabled={pending}
                    type="submit"
                  >
                    {pending ? "Saving…" : "Save"}
                  </button>
                </div>
              </form>
            </div>
          ) : null}
        </div>
      )}
      <section
        aria-busy={loading || paging}
        className="order-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="border-b border-slate-200 bg-slate-50/70 p-4 sm:p-5">
          <h2 className="mb-3 text-sm font-bold tracking-wide text-slate-700 uppercase">
            Membership directory
          </h2>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="grid gap-1.5 text-xs font-semibold text-slate-600">
              View
              <select
                aria-label="Membership list"
                className="input"
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
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-slate-600">
              Membership status
              <select
                aria-label="Filter by status"
                className="input"
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
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-slate-600">
              Payment status
              <select
                aria-label="Filter by payment status"
                className="input"
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
            </label>
          </div>
        </div>
        {loading ? (
          <div className="grid gap-3 p-5" role="status">
            <span className="sr-only">Loading memberships.</span>
            {[0, 1, 2].map((item) => (
              <div
                className="h-16 animate-pulse rounded-xl bg-slate-100"
                key={item}
              />
            ))}
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table aria-label="Memberships" className="w-full text-left text-sm">
            <thead className="bg-slate-800 text-white">
              <tr>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Member
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Plan
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Dates
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Total
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Status
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Note
                </th>
                <th className="px-5 py-3 text-xs font-semibold" scope="col">
                  Action
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {memberships.map((membership) => (
                <tr className="hover:bg-slate-50" key={membership.id}>
                  <td className="whitespace-nowrap px-5 py-4 font-semibold text-slate-900">
                    <IdentifierValue
                      value={memberTableLabel(
                        knownMembers.get(membership.member_id),
                        membership.member_id,
                      )}
                    />
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 text-slate-700">
                    {membership.plan_name}
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                    {membership.start_date} – {membership.expiry_date}
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 font-semibold text-slate-900">
                    {membership.final_amount} {membership.currency}
                  </td>
                  <td className="px-5 py-4">
                    <span className="sr-only">
                      {membership.status} / {membership.payment_status}
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      <StatusBadge status={membership.status} />
                      <StatusBadge status={membership.payment_status} />
                    </div>
                  </td>
                  <td className="max-w-56 truncate px-5 py-4 text-slate-600">
                    {membership.note ? membership.note : "No note"}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex flex-nowrap gap-2">
                      {canUpdate ? (
                        <button
                          aria-label={membershipNoteActionName(
                            membership,
                            memberTableLabel(
                              knownMembers.get(membership.member_id),
                              membership.member_id,
                            ),
                          )}
                          className={rowActionClass("edit")}
                          disabled={membershipActionsLocked}
                          onClick={() => openNoteEditor(membership)}
                          type="button"
                        >
                          Edit note
                        </button>
                      ) : null}
                      {canUpdate && membership.status === "ACTIVE" && (
                        <button
                          className={rowActionClass("warn")}
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
                          className={rowActionClass("success")}
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
                            className={rowActionClass("cancel")}
                            disabled={membershipActionsLocked}
                            onClick={() => setCancelTarget(membership)}
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
                  <td className="px-6 py-14 text-center" colSpan={7}>
                    <p className="font-semibold text-slate-700">
                      No memberships found.
                    </p>
                    <p className="mt-1 text-sm text-slate-500">
                      Try selecting a different status or expiry range.
                    </p>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {paging ? (
          <p className="m-4 text-center text-sm text-slate-500" role="status">
            Loading more memberships.
          </p>
        ) : null}
        {hasMore && nextAfter ? (
          <div className="flex justify-center border-t border-slate-100 p-4">
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
      {archiveTarget ? (
        <ArchivePlanDialog
          plan={archiveTarget}
          onCancel={() => setArchiveTarget(null)}
          onConfirm={() => void archivePlan(archiveTarget)}
        />
      ) : null}
      {cancelTarget ? (
        <CancelMembershipDialog
          memberLabel={memberTableLabel(
            knownMembers.get(cancelTarget.member_id),
            cancelTarget.member_id,
          )}
          planName={cancelTarget.plan_name}
          onCancel={() => setCancelTarget(null)}
          onConfirm={(reason) => {
            const target = cancelTarget;
            setCancelTarget(null);
            void membershipAction(target, "cancel", reason);
          }}
        />
      ) : null}
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
      {canCreate && showMembershipForm ? (
        <div
          className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget)
              closeStartMembershipForm();
          }}
        >
          <form
            aria-busy={pending}
            aria-labelledby={startMembershipTitleId}
            aria-modal="true"
            className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-3xl"
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.stopPropagation();
              if (pendingRef.current) return;
              event.preventDefault();
              closeStartMembershipForm();
            }}
            onSubmit={createMembership}
            role="dialog"
          >
            <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <span
                aria-hidden="true"
                className="grid min-h-9 shrink-0 place-items-center rounded-lg bg-amber-100 px-2 text-[0.65rem] font-black tracking-wide text-amber-800"
              >
                MEMBER
              </span>
              <div className="min-w-0 flex-1">
                <h2
                  className="text-xl font-bold text-slate-900"
                  id={startMembershipTitleId}
                >
                  Start membership
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Find a member, choose a plan, and confirm the start date.
                </p>
              </div>
              <button
                className="button button-cancel button-sm"
                disabled={pending}
                onClick={closeStartMembershipForm}
                type="button"
              >
                Close
              </button>
            </div>
            <div className="grid gap-5 px-5 py-4 sm:px-6">
              <fieldset className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <legend className="px-2 text-sm font-bold text-slate-700">
                  Member selection
                </legend>
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
                    ref={memberSearchRef}
                    value={memberQuery}
                  />
                </label>
                {memberSearchLoading ? (
                  <p className="text-sm text-slate-500" role="status">
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
                  <ul
                    aria-label="Matching members"
                    className="grid max-h-48 gap-2 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2"
                  >
                    {memberResults.map((member) => (
                      <li key={member.id}>
                        <button
                          aria-pressed={selectedMember?.id === member.id}
                          className="w-full rounded-lg px-3 py-2 text-left text-sm font-semibold text-slate-700 hover:bg-slate-100 aria-pressed:bg-amber-100 aria-pressed:text-amber-900"
                          onClick={() => selectMember(member)}
                          type="button"
                        >
                          {memberOptionLabel(member)}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p
                  className={
                    selectedMember
                      ? "rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm font-semibold text-emerald-800"
                      : "rounded-lg border border-dashed border-slate-300 px-3 py-2.5 text-sm text-slate-500"
                  }
                >
                  {selectedMember
                    ? `Selected member: ${memberOptionLabel(selectedMember)}`
                    : "No member selected."}
                </p>
              </fieldset>
              <div className="grid gap-4 sm:grid-cols-2 [&_.field]:text-sm [&_.field]:font-semibold [&_.field]:text-slate-700">
                <label className="field sm:col-span-2">
                  Plan
                  <select
                    className="input"
                    disabled={Boolean(planError)}
                    name="plan_id"
                    onChange={(event) => setDraftPlanId(event.target.value)}
                    required
                    value={draftPlanId}
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
                    name="start_date"
                    onChange={(event) => setDraftStartDate(event.target.value)}
                    required
                    type="date"
                    value={draftStartDate}
                  />
                </label>
                <label className="field">
                  Discount
                  <input
                    className="input"
                    min="0"
                    name="discount_amount"
                    onChange={(event) => setDraftDiscount(event.target.value)}
                    step="0.01"
                    type="number"
                    value={draftDiscount}
                  />
                </label>
              </div>
              <label className="field text-sm font-semibold text-slate-700">
                Note
                <textarea
                  className="input min-h-24"
                  maxLength={4000}
                  name="note"
                  onChange={(event) => setDraftNote(event.target.value)}
                  value={draftNote}
                />
              </label>
            </div>
            <div className="sticky bottom-0 z-10 border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
              {membershipFormError ? (
                <p className="error mb-3" role="alert">
                  {membershipFormError}
                </p>
              ) : null}
              <div>
                <button
                  className="button button-cancel w-full sm:w-auto"
                  disabled={pending}
                  onClick={closeStartMembershipForm}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button button-create w-full sm:w-auto"
                  disabled={pending}
                  type="submit"
                >
                  Start membership
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function ArchivePlanDialog({
  plan,
  onCancel,
  onConfirm,
}: {
  plan: MembershipPlan;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className="w-full max-w-md overflow-hidden"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-xs font-bold tracking-[0.16em] text-red-600 uppercase">
            Archive plan
          </p>
          <h2
            className="mt-1 text-xl font-black tracking-tight text-slate-900"
            id={titleId}
          >
            Archive {plan.name}?
          </h2>
        </div>
        <div className="p-5">
          <p className="text-sm leading-6 text-slate-600">
            This plan will no longer be available for new memberships. Existing
            memberships and historical records will remain unchanged.
          </p>
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
            {plan.name} ({plan.code})
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              className="button button-cancel"
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className="button button-archive"
              onClick={onConfirm}
              type="button"
            >
              Archive
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function CancelMembershipDialog({
  memberLabel,
  planName,
  onCancel,
  onConfirm,
}: {
  memberLabel: string;
  planName: string;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const titleId = useId();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("Enter a cancellation reason.");
      return;
    }
    onConfirm(trimmed);
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <form
        aria-labelledby={titleId}
        aria-modal="true"
        className="w-full max-w-md overflow-hidden"
        role="dialog"
        onSubmit={submit}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-xs font-bold tracking-[0.16em] text-red-600 uppercase">
            Cancel membership
          </p>
          <h2
            className="mt-1 text-xl font-black tracking-tight text-slate-900"
            id={titleId}
          >
            Cancel this membership?
          </h2>
        </div>
        <div className="grid gap-4 p-5">
          <p className="text-sm leading-6 text-slate-600">
            {memberLabel} will lose the {planName} membership. This does not
            delete payment history.
          </p>
          <label className="field" htmlFor={reasonId}>
            Cancellation reason
            <textarea
              className="input min-h-24"
              id={reasonId}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
              value={reason}
            />
          </label>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end">
            <button
              className="button button-cancel"
              onClick={onCancel}
              type="button"
            >
              Back
            </button>
            <button className="button button-delete" type="submit">
              Cancel membership
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tones: Record<string, string> = {
    ACTIVE: "bg-emerald-100 text-emerald-700",
    PAID: "bg-emerald-100 text-emerald-700",
    PENDING: "bg-amber-100 text-amber-800",
    PARTIAL: "bg-amber-100 text-amber-800",
    PARTIALLY_REFUNDED: "bg-amber-100 text-amber-800",
    SUSPENDED: "bg-orange-100 text-orange-700",
    EXPIRED: "bg-slate-100 text-slate-600",
    CANCELLED: "bg-red-100 text-red-700",
    UNPAID: "bg-red-100 text-red-700",
    REFUNDED: "bg-slate-100 text-slate-600",
    ARCHIVED: "bg-slate-100 text-slate-600",
  };
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${tones[status] ?? "bg-slate-100 text-slate-600"}`}
    >
      {label ?? statusLabel(status)}
    </span>
  );
}

function statusLabel(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
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
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto p-4 py-8"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel();
      }}
    >
      <div
        aria-busy={pending}
        aria-labelledby={titleId}
        aria-modal="true"
        className="grid max-h-[90vh] w-full max-w-xl overflow-y-auto"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !pending) onCancel();
        }}
      >
        <div className="border-b border-slate-200 px-5 py-4 sm:px-6">
          <p className="text-xs font-bold tracking-[0.16em] text-amber-600 uppercase">
            Membership plan
          </p>
          <h2
            className="mt-1 text-xl font-black tracking-tight text-slate-900"
            id={titleId}
          >
            {title}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Update the plan details below, then save your changes.
          </p>
        </div>
        <form className="grid gap-5 p-5 sm:p-6" onSubmit={onSubmit}>
          <div className="grid gap-4 sm:grid-cols-2 [&_.field]:text-sm [&_.field]:font-semibold [&_.field]:text-slate-700">
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
              <input
                className="input bg-slate-50 text-slate-500"
                readOnly
                value={source.currency}
              />
            </label>
          </div>
          <p className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-600">
            Status
            <StatusBadge
              label={planStatusLabel(source)}
              status={planStatusLabel(source).toUpperCase()}
            />
          </p>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4">
            <button
              className="button button-cancel"
              disabled={pending}
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              {pending ? "Saving…" : "Save"}
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
  const shortMember = compactIdentifier(state.memberLabel);
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
        <div>
          <h2 className="text-lg font-bold" id={titleId}>
            Edit membership note
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {shortMember} · {state.planName} · Membership{" "}
            <IdentifierValue value={state.membershipId} />
          </p>
        </div>
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
            <button
              aria-label={`Save note for ${shortMember} · ${state.planName}`}
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              Save
            </button>
            <button
              aria-label="Discard note changes"
              className="button button-cancel"
              disabled={pending}
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

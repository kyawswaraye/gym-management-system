"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  listAllMembershipPlans,
  listMembers,
  listMembershipExpiry,
  listMemberships,
  operationMutation,
} from "@/features/operations/client";
import type {
  Member,
  Membership,
  MembershipExpiryQuery,
  MembershipListPage,
  MembershipListQuery,
  MembershipPlan,
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
  const [archivingPlanIdList, setArchivingPlanIdList] = useState<
    readonly string[]
  >([]);
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
  const archivingPlanIds = useRef(new Set<string>());
  const memberSearchVersion = useRef(0);
  const canManagePlans = canCreate || canUpdate;
  const filters = { listMode, status, paymentStatus };

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

  async function archivePlan(target: MembershipPlan) {
    if (
      !canUpdate ||
      !target.is_active ||
      archivingPlanIds.current.has(target.id)
    ) {
      return;
    }
    archivingPlanIds.current.add(target.id);
    setArchivingPlanIdList([...archivingPlanIds.current]);
    if (!window.confirm(`Archive plan ${target.name} (${target.code})?`)) {
      archivingPlanIds.current.delete(target.id);
      setArchivingPlanIdList([...archivingPlanIds.current]);
      return;
    }
    setError("");
    setErrorScope(null);
    try {
      const archived = await operationMutation<MembershipPlan>(
        `membership-plans/${target.id}/archive/`,
        {},
      );
      setPlans((current) =>
        current.map((row) => (row.id === target.id ? archived : row)),
      );
    } catch (caught) {
      setError(errorText(caught, "Unable to archive plan."));
      setErrorScope(null);
    } finally {
      archivingPlanIds.current.delete(target.id);
      setArchivingPlanIdList([...archivingPlanIds.current]);
    }
  }

  function selectMember(member: Member) {
    setSelectedMember(member);
    setKnownMembers((current) => rememberMembers(current, [member]));
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
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
      setError(errorText(caught, "Unable to create plan."));
      setErrorScope(null);
    } finally {
      setPending(false);
    }
  }

  async function createMembership(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedMember) {
      setError("Select a member.");
      setErrorScope(null);
      return;
    }
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      await operationMutation<Membership>("memberships/", {
        member_id: selectedMember.id,
        plan_id: form.get("plan_id"),
        start_date: form.get("start_date"),
        discount_amount: form.get("discount_amount") || "0.00",
      });
      await refreshMemberships();
    } catch (caught) {
      setError(errorText(caught, "Unable to create membership."));
      setErrorScope(null);
    } finally {
      setPending(false);
    }
  }

  async function membershipAction(
    membership: Membership,
    action: "suspend" | "resume" | "cancel",
  ) {
    const body: { reason?: string } = {};
    if (action === "cancel") {
      const reason = window.prompt("Cancellation reason");
      if (reason === null || reason.trim() === "") return;
      body.reason = reason.trim();
    }
    setPending(true);
    try {
      await operationMutation(`memberships/${membership.id}/${action}/`, body);
      await refreshMemberships();
    } catch (caught) {
      setError(errorText(caught, "Unable to update membership."));
      setErrorScope(null);
    } finally {
      setPending(false);
    }
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
                          {plan.is_active ? (
                            <button
                              aria-label={planArchiveActionName(plan)}
                              className="underline"
                              disabled={archivingPlanIdList.includes(plan.id)}
                              onClick={() => void archivePlan(plan)}
                              type="button"
                            >
                              Archive {plan.name} plan
                            </button>
                          ) : null}
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
                    {canUpdate && membership.status === "ACTIVE" && (
                      <button
                        className="underline"
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
                          className="ml-3 underline"
                          onClick={() =>
                            void membershipAction(membership, "cancel")
                          }
                          type="button"
                        >
                          Cancel
                        </button>
                      )}
                  </td>
                </tr>
              ))}
              {!loading && !error && memberships.length === 0 ? (
                <tr>
                  <td className="muted p-6 text-center" colSpan={6}>
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
    </div>
  );
}

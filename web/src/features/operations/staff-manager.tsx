"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  activateStaff,
  getMembershipRoles,
  getStaff,
  inviteStaff,
  listActiveRoleCatalog,
  listStaff,
  replaceMembershipRoles,
  suspendStaff,
  updateStaff,
  type StaffListQuery,
} from "@/features/operations/client";
import {
  assignmentRequiresOwner,
  assignableRoles,
} from "@/features/operations/role-policy";
import type { StaffAccess } from "@/lib/auth/authorization";
import type {
  MembershipRole,
  Role,
  StaffInviteInput,
  StaffRecord,
  StaffUpdate,
} from "@/types/api";

const PROFILE_STATUSES = [
  "ACTIVE",
  "ON_LEAVE",
  "TERMINATED",
  "ARCHIVED",
] as const;
const MEMBERSHIP_STATUSES = [
  "INVITED",
  "ACTIVE",
  "SUSPENDED",
  "REVOKED",
] as const;
const STAFF_PAGE_LIMIT = 25;

function staffPageQuery(
  filters: StaffListQuery,
  after?: string,
): StaffListQuery {
  const next: StaffListQuery = { limit: STAFF_PAGE_LIMIT };
  if (filters.status) next.status = filters.status;
  if (filters.membership_status)
    next.membership_status = filters.membership_status;
  if (filters.search) next.search = filters.search;
  if (after) next.after = after;
  return next;
}

function appendStaff(
  current: StaffRecord[],
  incoming: StaffRecord[],
): StaffRecord[] {
  const seen = new Set(current.map((person) => person.id));
  return [
    ...current,
    ...incoming.filter((person) => {
      if (seen.has(person.id)) return false;
      seen.add(person.id);
      return true;
    }),
  ];
}

type FieldErrors = Record<string, string>;
type Checked = { ok: true; value: string } | { ok: false; message: string };
type InviteResult =
  | { ok: true; body: StaffInviteInput }
  | { ok: false; errors: FieldErrors; message: string };
type ProfileResult =
  | { ok: true; body: StaffUpdate }
  | { ok: false; errors: FieldErrors; message: string };
type LifecycleAction = "activate" | "suspend";

function failureState(
  failure: unknown,
  fallback: string,
): { message: string; fields: FieldErrors } {
  if (!(failure instanceof Error)) return { message: fallback, fields: {} };
  const candidate =
    "fields" in failure ? (failure as { fields?: unknown }).fields : undefined;
  const fields: FieldErrors = {};
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    for (const [key, value] of Object.entries(candidate)) {
      if (typeof value === "string" && value) fields[key] = value;
    }
  }
  return { message: failure.message || fallback, fields };
}

function textValue(
  raw: FormDataEntryValue | null,
  label: string,
  max: number,
  required: boolean,
): Checked {
  const value = String(raw ?? "").trim();
  if (required && !value) {
    return { ok: false, message: `Enter a ${label.toLowerCase()}.` };
  }
  if (value.length > max) {
    return {
      ok: false,
      message: `Enter a ${label.toLowerCase()} up to ${max} characters.`,
    };
  }
  return { ok: true, value };
}

function emailValue(raw: FormDataEntryValue | null): Checked {
  const value = String(raw ?? "").trim();
  if (
    !value ||
    value.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
  ) {
    return { ok: false, message: "Enter a valid email address." };
  }
  return { ok: true, value };
}

function staffCodeValue(raw: FormDataEntryValue | null): Checked {
  const value = String(raw ?? "").trim();
  if (!value || /\s/u.test(value) || value.length > 32) {
    return {
      ok: false,
      message: "Enter a staff code without spaces, up to 32 characters.",
    };
  }
  return { ok: true, value };
}

function dateValue(raw: FormDataEntryValue | null): Checked {
  const value = String(raw ?? "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) {
    return { ok: false, message: "Enter a joined date." };
  }
  return { ok: true, value };
}

function commissionValue(raw: FormDataEntryValue | null): Checked {
  const value = String(raw ?? "").trim();
  if (!value) return { ok: true, value: "" };
  if (!/^\d{1,3}(?:\.\d{1,2})?$/.test(value) || Number(value) > 100) {
    return {
      ok: false,
      message: "Enter a commission from 0 to 100 with up to 2 decimal places.",
    };
  }
  return { ok: true, value };
}

function collectErrors(checks: Record<string, Checked>): FieldErrors {
  return Object.fromEntries(
    Object.entries(checks)
      .filter((entry): entry is [string, { ok: false; message: string }] => {
        return !entry[1].ok;
      })
      .map(([key, check]) => [key, check.message]),
  );
}

function readInvite(form: FormData, roles: Role[]): InviteResult {
  const checks = {
    email: emailValue(form.get("email")),
    first_name: textValue(form.get("first_name"), "First name", 150, true),
    last_name: textValue(form.get("last_name"), "Last name", 150, true),
    phone: textValue(form.get("phone"), "Phone", 32, false),
    staff_code: staffCodeValue(form.get("staff_code")),
    display_name: textValue(
      form.get("display_name"),
      "Display name",
      150,
      true,
    ),
    joined_date: dateValue(form.get("joined_date")),
    employment_title: textValue(
      form.get("employment_title"),
      "Employment title",
      100,
      false,
    ),
    default_commission_percent: commissionValue(
      form.get("default_commission_percent"),
    ),
  };
  const errors = collectErrors(checks);
  const allowed = new Set(roles.map((role) => role.id));
  const roleIds = [
    ...new Set(
      form
        .getAll("role_ids")
        .map(String)
        .filter((id) => allowed.has(id)),
    ),
  ];
  if (roleIds.length === 0) {
    errors.role_ids = "Select at least one role.";
  }
  if (Object.keys(errors).length > 0) {
    return {
      ok: false,
      errors,
      message: Object.values(errors)[0] ?? "Check the invitation.",
    };
  }
  if (
    !checks.email.ok ||
    !checks.first_name.ok ||
    !checks.last_name.ok ||
    !checks.phone.ok ||
    !checks.staff_code.ok ||
    !checks.display_name.ok ||
    !checks.joined_date.ok ||
    !checks.employment_title.ok ||
    !checks.default_commission_percent.ok
  ) {
    return { ok: false, errors, message: "Check the invitation." };
  }
  const body: StaffInviteInput = {
    email: checks.email.value,
    first_name: checks.first_name.value,
    last_name: checks.last_name.value,
    staff_code: checks.staff_code.value,
    display_name: checks.display_name.value,
    joined_date: checks.joined_date.value,
    role_ids: roleIds,
  };
  if (checks.phone.value) body.phone = checks.phone.value;
  if (checks.employment_title.value) {
    body.employment_title = checks.employment_title.value;
  }
  if (checks.default_commission_percent.value) {
    body.default_commission_percent = checks.default_commission_percent.value;
  }
  return { ok: true, body };
}

function readProfile(form: FormData): ProfileResult {
  const checks = {
    staff_code: staffCodeValue(form.get("staff_code")),
    display_name: textValue(
      form.get("display_name"),
      "Display name",
      150,
      true,
    ),
    phone: textValue(form.get("phone"), "Phone", 32, false),
    joined_date: dateValue(form.get("joined_date")),
    employment_title: textValue(
      form.get("employment_title"),
      "Employment title",
      100,
      false,
    ),
    default_commission_percent: commissionValue(
      form.get("default_commission_percent"),
    ),
  };
  const errors = collectErrors(checks);
  if (
    checks.default_commission_percent.ok &&
    !checks.default_commission_percent.value
  ) {
    errors.default_commission_percent =
      "Enter a commission from 0 to 100 with up to 2 decimal places.";
  }
  if (Object.keys(errors).length > 0) {
    return {
      ok: false,
      errors,
      message: Object.values(errors)[0] ?? "Check the staff profile.",
    };
  }
  if (
    !checks.staff_code.ok ||
    !checks.display_name.ok ||
    !checks.phone.ok ||
    !checks.joined_date.ok ||
    !checks.employment_title.ok ||
    !checks.default_commission_percent.ok
  ) {
    return { ok: false, errors, message: "Check the staff profile." };
  }
  return {
    ok: true,
    body: {
      staff_code: checks.staff_code.value,
      display_name: checks.display_name.value,
      phone: checks.phone.value,
      joined_date: checks.joined_date.value,
      employment_title: checks.employment_title.value,
      default_commission_percent: checks.default_commission_percent.value,
    },
  };
}

function InputField({
  label,
  name,
  error,
  type = "text",
  required = false,
  defaultValue = "",
  maxLength,
  inputMode,
}: {
  label: string;
  name: string;
  error?: string | undefined;
  type?: string;
  required?: boolean;
  defaultValue?: string;
  maxLength?: number;
  inputMode?: "decimal" | "email" | "tel" | "text";
}) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        name={name}
        type={type}
        required={required}
        defaultValue={defaultValue}
        maxLength={maxLength}
        inputMode={inputMode}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <span id={errorId} className="error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function StaffFacts({
  staff,
  includeProfile,
}: {
  staff: StaffRecord;
  includeProfile: boolean;
}) {
  const rows: { label: string; value: string }[] = [
    { label: "Email", value: staff.membership.email },
    {
      label: "Name",
      value:
        `${staff.membership.first_name} ${staff.membership.last_name}`.trim(),
    },
    { label: "Profile status", value: staff.status },
    { label: "Membership status", value: staff.membership.status },
    { label: "Assigned roles", value: String(staff.role_ids.length) },
    {
      label: "Pending invitation roles",
      value: String(staff.invited_role_ids.length),
    },
  ];
  if (includeProfile) {
    rows.splice(
      2,
      0,
      { label: "Staff code", value: staff.staff_code },
      { label: "Phone", value: staff.phone || "—" },
      { label: "Employment title", value: staff.employment_title || "—" },
      { label: "Joined date", value: staff.joined_date },
      {
        label: "Default commission percent",
        value: staff.default_commission_percent,
      },
    );
  }
  if (staff.terminated_date) {
    rows.push({ label: "Terminated date", value: staff.terminated_date });
  }
  if (staff.archived_at) {
    rows.push({ label: "Archived at", value: staff.archived_at });
  }
  return (
    <dl className="mt-4 grid gap-2 text-sm">
      {rows.map((row) => (
        <div className="grid gap-1 sm:grid-cols-[12rem_1fr]" key={row.label}>
          <dt className="muted">{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function LifecycleDialog({
  staffName,
  action,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  staffName: string;
  action: LifecycleAction;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const titleId = useId();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const title =
    action === "suspend" ? "Suspend staff access" : "Activate staff access";
  const confirmLabel =
    action === "suspend" ? "Confirm suspension" : "Confirm activation";

  useEffect(() => {
    reasonRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-20 grid place-items-center bg-slate-950/40 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        className="card w-full max-w-md"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <h2 id={titleId} className="text-xl font-bold">
          {title}
        </h2>
        <p className="muted mt-2">
          {action === "suspend" ? "Suspend" : "Activate"} login access for{" "}
          {staffName}. This does not terminate or archive the profile.
        </p>
        <form className="mt-4 grid gap-3" noValidate onSubmit={onConfirm}>
          {error ? (
            <p className="error" id="staff-lifecycle-error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="field">
            <label htmlFor="staff-lifecycle-reason">Reason</label>
            <textarea
              ref={reasonRef}
              id="staff-lifecycle-reason"
              className="input min-h-24"
              name="reason"
              required
              minLength={3}
              maxLength={500}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "staff-lifecycle-error" : undefined}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="button" disabled={pending} type="submit">
              {pending ? "Saving…" : confirmLabel}
            </button>
            <button
              className="button bg-white text-slate-900 ring-1 ring-slate-300"
              type="button"
              onClick={onCancel}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Banner({
  loading,
  error,
  success,
  empty,
}: {
  loading: boolean;
  error: string | null;
  success: string | null;
  empty: boolean;
}) {
  let banner: ReactNode = null;
  if (loading) banner = <p role="status">Loading staff.</p>;
  else if (error) {
    banner = (
      <p className="error" role="alert">
        {error}
      </p>
    );
  } else if (success) banner = <p role="status">{success}</p>;
  else if (empty) {
    banner = <p role="status">No staff records match this view.</p>;
  }
  return banner ? <div className="mt-4">{banner}</div> : null;
}

const OWNER_REQUIRED = "An Owner is required to change this role assignment.";

export function StaffManager({
  canView,
  canManage,
  canViewRoles,
  isOwner,
  permissions,
}: StaffAccess) {
  const allowMutations = canView && canManage;
  const allowInvite = allowMutations && canViewRoles;
  const [staff, setStaff] = useState<StaffRecord[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [assignedRoles, setAssignedRoles] = useState<MembershipRole | null>(
    null,
  );
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [assignmentMessage, setAssignmentMessage] = useState<string | null>(
    null,
  );
  const [selected, setSelected] = useState<StaffRecord | null>(null);
  const [query, setQuery] = useState<StaffListQuery>({});
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [paging, setPaging] = useState(false);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const [loading, setLoading] = useState(true);
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rolesError, setRolesError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [inviteErrors, setInviteErrors] = useState<FieldErrors>({});
  const [profileErrors, setProfileErrors] = useState<FieldErrors>({});
  const [lifecycle, setLifecycle] = useState<{
    staff: StaffRecord;
    action: LifecycleAction;
  } | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  useEffect(() => {
    const requestId = ++listRequest.current;
    let active = true;
    void listStaff(staffPageQuery({}))
      .then((page) => {
        if (!active || requestId !== listRequest.current) return;
        setStaff(page.data);
        setNextAfter(page.meta.next_after);
      })
      .catch((failure: unknown) => {
        if (active && requestId === listRequest.current)
          setError(failureState(failure, "Unable to load staff.").message);
      })
      .finally(() => {
        if (active && requestId === listRequest.current) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!allowInvite) return;
    let active = true;
    void listActiveRoleCatalog()
      .then((choices) => {
        if (!active) return;
        setRoles(
          choices.filter(
            (role) => role.is_active && role.key.toUpperCase() !== "OWNER",
          ),
        );
      })
      .catch((failure: unknown) => {
        if (active) {
          setRolesError(failureState(failure, "Unable to load roles.").message);
        }
      })
      .finally(() => {
        if (active) setRolesLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [allowInvite]);

  async function reload(next: StaffListQuery = query) {
    const requestId = ++listRequest.current;
    const filters = staffPageQuery(next);
    const page = await listStaff(filters);
    if (requestId !== listRequest.current) return;
    setStaff(page.data);
    setNextAfter(page.meta.next_after);
    setQuery(filters);
  }

  async function loadMore() {
    if (!nextAfter || paging) return;
    const requestId = ++listRequest.current;
    const cursor = nextAfter;
    const filters = query;
    setPaging(true);
    setError(null);
    try {
      const page = await listStaff(staffPageQuery(filters, cursor));
      if (requestId !== listRequest.current) return;
      setStaff((current) => appendStaff(current, page.data));
      setNextAfter(page.meta.next_after);
    } catch (failure) {
      if (requestId === listRequest.current) {
        setError(failureState(failure, "Unable to load staff.").message);
      }
    } finally {
      if (requestId === listRequest.current) setPaging(false);
    }
  }

  async function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next: StaffListQuery = {};
    const search = String(form.get("search") ?? "").trim();
    const status = String(form.get("status") ?? "");
    const membershipStatus = String(form.get("membership_status") ?? "");
    if (search) next.search = search;
    if (status) next.status = status;
    if (membershipStatus) next.membership_status = membershipStatus;
    setError(null);
    setSuccess(null);
    setStaff([]);
    setNextAfter(null);
    setLoading(true);
    try {
      await reload(next);
    } catch (failure) {
      setError(failureState(failure, "Unable to load staff.").message);
    } finally {
      setLoading(false);
    }
  }

  async function openDetails(id: string) {
    const requestId = ++detailRequest.current;
    setDetailLoading(true);
    setProfileErrors({});
    setAssignmentError(null);
    setAssignmentMessage(null);
    setAssignedRoles(null);
    setError(null);
    try {
      const person = await getStaff(id);
      if (requestId !== detailRequest.current) return;
      setSelected(person);
      if (allowInvite) {
        setAssignmentLoading(true);
        try {
          const membershipRoles = await getMembershipRoles(
            person.membership.gym_membership_id,
          );
          if (requestId !== detailRequest.current) return;
          setAssignedRoles(membershipRoles);
        } catch (failure) {
          if (requestId === detailRequest.current) {
            setAssignmentError(
              failureState(failure, "Unable to load assigned roles.").message,
            );
          }
        } finally {
          if (requestId === detailRequest.current) setAssignmentLoading(false);
        }
      }
    } catch (failure) {
      if (requestId === detailRequest.current) {
        setError(
          failureState(failure, "Unable to load staff details.").message,
        );
      }
    } finally {
      if (requestId === detailRequest.current) setDetailLoading(false);
    }
  }

  const offeredRoles = assignableRoles(roles, {
    isOwner,
    livePermissions: permissions,
  });
  const assignmentBlocked =
    rolesLoaded &&
    !rolesError &&
    assignedRoles !== null &&
    assignmentRequiresOwner(assignedRoles.roles, roles, {
      isOwner,
      livePermissions: permissions,
    });

  async function submitAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !assignedRoles) return;
    const allowed = new Set(offeredRoles.map((role) => role.id));
    if (
      assignmentRequiresOwner(assignedRoles.roles, roles, {
        isOwner,
        livePermissions: permissions,
      })
    ) {
      setAssignmentError(OWNER_REQUIRED);
      setAssignmentMessage(null);
      return;
    }
    const roleIds = [
      ...new Set(
        new FormData(event.currentTarget)
          .getAll("role_ids")
          .map(String)
          .filter((id) => allowed.has(id)),
      ),
    ];
    if (roleIds.length === 0) {
      setAssignmentError("Select at least one role.");
      setAssignmentMessage(null);
      return;
    }
    setPending(true);
    setAssignmentError(null);
    setAssignmentMessage(null);
    setError(null);
    try {
      const result = await replaceMembershipRoles(
        selected.membership.gym_membership_id,
        roleIds,
      );
      const visibleRoles = result.roles.filter((role) => role.key !== "OWNER");
      setAssignedRoles(result);
      setSelected((current) =>
        current &&
        current.membership.gym_membership_id === result.gym_membership_id
          ? {
              ...current,
              role_ids: visibleRoles.map((role) => role.id),
              membership: {
                ...current.membership,
                authz_version: result.authz_version,
              },
            }
          : current,
      );
      setAssignmentMessage(
        `Roles saved. Authorization version ${result.authz_version}.`,
      );
    } catch (failure) {
      setAssignmentError(
        failureState(failure, "Unable to update assigned roles.").message,
      );
    } finally {
      setPending(false);
    }
  }

  async function submitInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = readInvite(new FormData(form), offeredRoles);
    if (!parsed.ok) {
      setInviteErrors(parsed.errors);
      setError(parsed.message);
      setSuccess(null);
      return;
    }
    setPending(true);
    setInviteErrors({});
    setError(null);
    setSuccess(null);
    try {
      const result = await inviteStaff(parsed.body);
      setSuccess(`Invitation sent to ${result.email}.`);
      form.reset();
      await reload();
    } catch (failure) {
      const state = failureState(failure, "Unable to invite staff.");
      setError(state.message);
      setInviteErrors(state.fields);
    } finally {
      setPending(false);
    }
  }

  async function submitProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const parsed = readProfile(new FormData(event.currentTarget));
    if (!parsed.ok) {
      setProfileErrors(parsed.errors);
      setError(parsed.message);
      setSuccess(null);
      return;
    }
    setPending(true);
    setProfileErrors({});
    setError(null);
    setSuccess(null);
    try {
      const updated = await updateStaff(selected.id, parsed.body);
      setSelected(updated);
      setSuccess("Staff profile updated.");
      await reload();
    } catch (failure) {
      const state = failureState(failure, "Unable to update staff profile.");
      setError(state.message);
      setProfileErrors(state.fields);
    } finally {
      setPending(false);
    }
  }

  async function confirmLifecycle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lifecycle) return;
    const reason = String(
      new FormData(event.currentTarget).get("reason") ?? "",
    ).trim();
    if (reason.length < 3 || reason.length > 500) {
      setLifecycleError(
        reason.length > 500
          ? "Enter a reason up to 500 characters."
          : "Enter a reason of at least 3 characters.",
      );
      return;
    }
    setPending(true);
    setLifecycleError(null);
    setError(null);
    try {
      const updated =
        lifecycle.action === "activate"
          ? await activateStaff(lifecycle.staff.id, reason)
          : await suspendStaff(lifecycle.staff.id, reason);
      setSelected(updated);
      setSuccess(
        lifecycle.action === "activate"
          ? "Staff access activated."
          : "Staff access suspended.",
      );
      setLifecycle(null);
      await reload();
    } catch (failure) {
      const state = failureState(failure, "Unable to update staff access.");
      setLifecycleError(state.fields.reason ?? state.message);
    } finally {
      setPending(false);
    }
  }

  const locked = selected
    ? selected.status === "TERMINATED" || selected.status === "ARCHIVED"
    : false;

  return (
    <div className="grid gap-6">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => void applyFilters(event)}
      >
        <label className="field min-w-56 flex-1">
          <span>Search staff</span>
          <input className="input" name="search" maxLength={100} />
        </label>
        <label className="field">
          <span>Profile status</span>
          <select className="input" name="status" defaultValue="">
            <option value="">All profile statuses</option>
            {PROFILE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Membership status</span>
          <select className="input" name="membership_status" defaultValue="">
            <option value="">All membership statuses</option>
            {MEMBERSHIP_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <button className="button" type="submit">
          Apply filters
        </button>
      </form>

      <Banner
        loading={loading && staff.length === 0}
        error={error}
        success={success}
        empty={!loading && staff.length === 0 && !error && !success}
      />

      {allowMutations && !canViewRoles ? (
        <p className="muted">
          Inviting staff requires permission to view roles.
        </p>
      ) : null}

      {allowInvite ? (
        <section
          className="rounded-lg border border-slate-200 bg-white p-5"
          role="region"
          aria-labelledby="invite-staff-title"
        >
          <h2 id="invite-staff-title" className="text-xl font-bold">
            Invite staff
          </h2>
          {!rolesLoaded ? (
            <p className="mt-4" role="status">
              Loading roles.
            </p>
          ) : null}
          {rolesError ? (
            <p className="error mt-4" role="alert">
              {rolesError}
            </p>
          ) : null}
          {rolesLoaded && !rolesError && offeredRoles.length === 0 ? (
            <p className="muted mt-4">No assignable roles are available.</p>
          ) : null}
          {rolesLoaded && !rolesError && offeredRoles.length > 0 ? (
            <form
              className="mt-4 grid gap-3 md:grid-cols-2"
              noValidate
              onSubmit={(event) => void submitInvite(event)}
            >
              <InputField
                label="Email"
                name="email"
                type="email"
                required
                maxLength={254}
                error={inviteErrors.email}
                inputMode="email"
              />
              <InputField
                label="First name"
                name="first_name"
                required
                maxLength={150}
                error={inviteErrors.first_name}
              />
              <InputField
                label="Last name"
                name="last_name"
                required
                maxLength={150}
                error={inviteErrors.last_name}
              />
              <InputField
                label="Phone"
                name="phone"
                type="tel"
                maxLength={32}
                error={inviteErrors.phone}
                inputMode="tel"
              />
              <InputField
                label="Staff code"
                name="staff_code"
                required
                maxLength={32}
                error={inviteErrors.staff_code}
              />
              <InputField
                label="Display name"
                name="display_name"
                required
                maxLength={150}
                error={inviteErrors.display_name}
              />
              <InputField
                label="Joined date"
                name="joined_date"
                type="date"
                required
                error={inviteErrors.joined_date}
              />
              <InputField
                label="Employment title"
                name="employment_title"
                maxLength={100}
                error={inviteErrors.employment_title}
              />
              <InputField
                label="Default commission percent"
                name="default_commission_percent"
                inputMode="decimal"
                error={inviteErrors.default_commission_percent}
              />
              <fieldset className="grid gap-2 md:col-span-2">
                <legend className="font-semibold">Roles</legend>
                {offeredRoles.map((role) => (
                  <div key={role.id}>
                    <label className="flex items-center gap-2">
                      <input name="role_ids" type="checkbox" value={role.id} />
                      <span>{role.name}</span>
                    </label>
                    {role.description ? (
                      <p className="muted text-sm">{role.description}</p>
                    ) : null}
                  </div>
                ))}
                {inviteErrors.role_ids ? (
                  <p className="error">{inviteErrors.role_ids}</p>
                ) : null}
              </fieldset>
              <button
                className="button md:col-span-2"
                disabled={pending}
                type="submit"
              >
                {pending ? "Sending…" : "Send invitation"}
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <caption className="px-4 py-3 text-left text-lg font-bold">
            Staff records
          </caption>
          <thead className="bg-slate-50">
            <tr>
              <th className="p-3" scope="col">
                Staff
              </th>
              <th className="p-3" scope="col">
                Email
              </th>
              <th className="p-3" scope="col">
                Profile
              </th>
              <th className="p-3" scope="col">
                Membership
              </th>
              <th className="p-3" scope="col">
                <span className="sr-only">Details</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {staff.map((person) => (
              <tr className="border-t border-slate-100" key={person.id}>
                <td className="p-3">
                  <strong>{person.display_name}</strong>
                  <span className="muted block">{person.staff_code}</span>
                </td>
                <td className="p-3">{person.membership.email}</td>
                <td className="p-3">{person.status}</td>
                <td className="p-3">{person.membership.status}</td>
                <td className="p-3">
                  <button
                    className="button"
                    type="button"
                    onClick={() => void openDetails(person.id)}
                  >
                    View details for {person.display_name}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {nextAfter ? (
        <button
          className="button"
          type="button"
          disabled={paging || pending}
          onClick={() => void loadMore()}
        >
          {paging ? "Loading more…" : "Load more"}
        </button>
      ) : null}

      {detailLoading ? <p role="status">Loading staff details.</p> : null}
      {selected ? (
        <section
          className="rounded-lg border border-slate-200 bg-white p-5"
          role="region"
          aria-labelledby="staff-detail-title"
        >
          <h2 id="staff-detail-title" className="text-xl font-bold">
            {selected.display_name}
          </h2>
          <StaffFacts staff={selected} includeProfile={!allowMutations} />
          {allowMutations ? (
            <form
              className="mt-4 grid gap-3 md:grid-cols-2"
              key={`${selected.id}:${selected.staff_code}:${selected.display_name}:${selected.phone}:${selected.joined_date}:${selected.employment_title}:${selected.default_commission_percent}`}
              noValidate
              onSubmit={(event) => void submitProfile(event)}
            >
              <InputField
                label="Staff code"
                name="staff_code"
                required
                maxLength={32}
                defaultValue={selected.staff_code}
                error={profileErrors.staff_code}
              />
              <InputField
                label="Display name"
                name="display_name"
                required
                maxLength={150}
                defaultValue={selected.display_name}
                error={profileErrors.display_name}
              />
              <InputField
                label="Phone"
                name="phone"
                type="tel"
                maxLength={32}
                defaultValue={selected.phone}
                error={profileErrors.phone}
                inputMode="tel"
              />
              <InputField
                label="Joined date"
                name="joined_date"
                type="date"
                required
                defaultValue={selected.joined_date}
                error={profileErrors.joined_date}
              />
              <InputField
                label="Employment title"
                name="employment_title"
                maxLength={100}
                defaultValue={selected.employment_title}
                error={profileErrors.employment_title}
              />
              <InputField
                label="Default commission percent"
                name="default_commission_percent"
                required
                defaultValue={selected.default_commission_percent}
                error={profileErrors.default_commission_percent}
                inputMode="decimal"
              />
              <button
                className="button md:col-span-2"
                disabled={pending}
                type="submit"
              >
                {pending ? "Saving…" : "Save staff profile"}
              </button>
            </form>
          ) : null}
          {allowMutations &&
          !locked &&
          selected.membership.status === "ACTIVE" ? (
            <button
              className="button mt-4"
              type="button"
              disabled={pending}
              onClick={() => {
                setLifecycleError(null);
                setLifecycle({ staff: selected, action: "suspend" });
              }}
            >
              Suspend access
            </button>
          ) : null}
          {allowMutations &&
          !locked &&
          selected.membership.status === "SUSPENDED" ? (
            <button
              className="button mt-4"
              type="button"
              disabled={pending}
              onClick={() => {
                setLifecycleError(null);
                setLifecycle({ staff: selected, action: "activate" });
              }}
            >
              Activate access
            </button>
          ) : null}
          {allowMutations && !canViewRoles ? (
            <p className="muted mt-4">
              Assigning roles requires permission to view roles.
            </p>
          ) : null}
          {allowInvite ? (
            <section className="mt-6" aria-labelledby="assign-roles-title">
              <h3 id="assign-roles-title" className="text-lg font-bold">
                Assign roles
              </h3>
              {assignmentLoading || !rolesLoaded ? (
                <p className="mt-3" role="status">
                  Loading assigned roles.
                </p>
              ) : null}
              {assignmentError ? (
                <p className="error mt-3" role="alert">
                  {assignmentError}
                </p>
              ) : null}
              {assignmentMessage ? (
                <p className="mt-3" role="status">
                  {assignmentMessage}
                </p>
              ) : null}
              {rolesError ? (
                <p className="error mt-3" role="alert">
                  {rolesError}
                </p>
              ) : null}
              {assignedRoles ? (
                <p className="mt-3">
                  Authorization version {assignedRoles.authz_version}
                </p>
              ) : null}
              {assignedRoles &&
              !assignmentBlocked &&
              assignedRoles.roles.every((role) => role.key === "OWNER") ? (
                <p className="muted mt-2">No roles are assigned.</p>
              ) : null}
              {assignedRoles &&
              assignedRoles.roles.filter((role) => role.key !== "OWNER")
                .length > 0 ? (
                <ul className="mt-2" aria-label="Current roles">
                  {assignedRoles.roles
                    .filter((role) => role.key !== "OWNER")
                    .map((role) => {
                      const retained = !offeredRoles.some(
                        (offered) => offered.id === role.id,
                      );
                      return (
                        <li key={role.id}>
                          {retained ? (
                            <label className="flex items-center gap-2">
                              <input
                                type="checkbox"
                                defaultChecked
                                disabled
                                readOnly
                              />
                              <span>{role.name}</span>
                              <span className="muted">
                                Outside your delegation boundary.
                              </span>
                            </label>
                          ) : (
                            role.name
                          )}
                        </li>
                      );
                    })}
                </ul>
              ) : null}
              {rolesLoaded && !rolesError && assignmentBlocked ? (
                <p className="mt-3" role="status">
                  {OWNER_REQUIRED}
                </p>
              ) : null}
              {rolesLoaded &&
              !rolesError &&
              !assignmentBlocked &&
              offeredRoles.length === 0 ? (
                <p className="muted mt-3">No assignable roles are available.</p>
              ) : null}
              {rolesLoaded &&
              !rolesError &&
              !assignmentBlocked &&
              offeredRoles.length > 0 &&
              assignedRoles ? (
                <form
                  className="mt-4 grid gap-2"
                  key={`${selected.membership.gym_membership_id}:${assignedRoles.authz_version}:${assignedRoles.roles.map((role) => role.id).join(",")}`}
                  noValidate
                  onSubmit={(event) => void submitAssignment(event)}
                >
                  <fieldset className="grid gap-2">
                    <legend className="font-semibold">Role choices</legend>
                    {offeredRoles.map((role) => (
                      <label key={role.id} className="flex items-center gap-2">
                        <input
                          name="role_ids"
                          type="checkbox"
                          value={role.id}
                          defaultChecked={assignedRoles.roles.some(
                            (assigned) => assigned.id === role.id,
                          )}
                        />
                        <span>{role.name}</span>
                      </label>
                    ))}
                  </fieldset>
                  <button className="button" disabled={pending} type="submit">
                    {pending ? "Saving…" : "Save assigned roles"}
                  </button>
                </form>
              ) : null}
            </section>
          ) : null}
        </section>
      ) : null}

      {lifecycle ? (
        <LifecycleDialog
          staffName={lifecycle.staff.display_name}
          action={lifecycle.action}
          pending={pending}
          error={lifecycleError}
          onCancel={() => {
            if (!pending) setLifecycle(null);
          }}
          onConfirm={(event) => void confirmLifecycle(event)}
        />
      ) : null}
    </div>
  );
}

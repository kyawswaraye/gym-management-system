"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";

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
import { FormDialog } from "@/components/form-dialog";
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
    staff_code: "",
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
      <label className="text-sm font-semibold text-slate-700" htmlFor={id}>
        {label}
      </label>
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

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Active",
  ON_LEAVE: "On leave",
  TERMINATED: "Terminated",
  ARCHIVED: "Archived",
  INVITED: "Invited",
  SUSPENDED: "Suspended",
  REVOKED: "Revoked",
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function statusTone(status: string): string {
  switch (status) {
    case "ACTIVE":
      return "bg-emerald-50 text-emerald-700 ring-emerald-600/15";
    case "INVITED":
      return "bg-sky-50 text-sky-700 ring-sky-600/15";
    case "ON_LEAVE":
    case "SUSPENDED":
      return "bg-amber-50 text-amber-800 ring-amber-600/15";
    case "TERMINATED":
    case "REVOKED":
      return "bg-rose-50 text-rose-700 ring-rose-600/15";
    default:
      return "bg-slate-100 text-slate-600 ring-slate-500/10";
  }
}

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${statusTone(status)}`}
    >
      {statusLabel(status)}
    </span>
  );
}

function Fact({
  label,
  value,
  suffix,
}: {
  label: string;
  value: string;
  suffix?: string;
}) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <dt className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
        {label}
      </dt>
      <dd className="mt-1 text-sm font-semibold break-words text-slate-900">
        <span>{value}</span>
        {suffix ? (
          <span className="font-medium text-slate-400">{suffix}</span>
        ) : null}
      </dd>
    </div>
  );
}

function ProfileFacts({ staff }: { staff: StaffRecord }) {
  const facts: { label: string; value: string; suffix?: string }[] = [
    { label: "Email", value: staff.membership.email },
    { label: "Phone", value: staff.phone || "—" },
    { label: "Employment title", value: staff.employment_title || "—" },
    { label: "Joined date", value: staff.joined_date },
    {
      label: "Commission",
      value: staff.default_commission_percent,
      suffix: "%",
    },
    { label: "Assigned roles", value: String(staff.role_ids.length) },
  ];
  if (staff.invited_role_ids.length > 0) {
    facts.push({
      label: "Pending invitation roles",
      value: String(staff.invited_role_ids.length),
    });
  }
  if (staff.terminated_date) {
    facts.push({ label: "Terminated date", value: staff.terminated_date });
  }
  if (staff.archived_at) {
    facts.push({ label: "Archived at", value: staff.archived_at });
  }
  return (
    <dl className="mt-5 grid gap-2 sm:grid-cols-2">
      {facts.map((fact) => (
        <Fact
          key={fact.label}
          label={fact.label}
          suffix={fact.suffix}
          value={fact.value}
        />
      ))}
    </dl>
  );
}

function RoleOption({
  role,
  defaultChecked = false,
  disabled = false,
  note,
}: {
  role: { id: string; name: string; description?: string };
  defaultChecked?: boolean;
  disabled?: boolean;
  note?: string;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-xl border px-3 py-3 ${
        disabled
          ? "border-slate-200 bg-slate-50"
          : "border-slate-200 bg-white has-[:checked]:border-emerald-300 has-[:checked]:bg-emerald-50"
      }`}
    >
      <input
        aria-label={role.name}
        className="mt-1 size-4 accent-emerald-700"
        defaultChecked={defaultChecked}
        disabled={disabled}
        name={disabled ? undefined : "role_ids"}
        readOnly={disabled || undefined}
        type="checkbox"
        value={role.id}
      />
      <span className="min-w-0">
        <span className="block font-semibold text-slate-900">{role.name}</span>
        {role.description ? (
          <span className="mt-0.5 block text-sm leading-5 text-slate-500">
            {role.description}
          </span>
        ) : null}
        {note ? (
          <span className="mt-1 block text-sm text-slate-500">{note}</span>
        ) : null}
      </span>
    </label>
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

  const description =
    action === "suspend"
      ? `Suspend login access for ${staffName}. The profile stays in place.`
      : `Activate login access for ${staffName}. The profile stays in place.`;

  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel();
      }}
      role="presentation"
    >
      <form
        aria-describedby={`${titleId}-description`}
        aria-labelledby={titleId}
        aria-modal="true"
        className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,32rem)] sm:max-w-lg"
        noValidate
        onKeyDown={(event) => {
          if (event.key !== "Escape" || pending) return;
          event.preventDefault();
          onCancel();
        }}
        onSubmit={onConfirm}
        role="dialog"
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {title}
            </h2>
            <p
              className="mt-1 text-sm leading-6 text-slate-500"
              id={`${titleId}-description`}
            >
              {description}
            </p>
          </div>
          <button
            className="button button-cancel button-sm"
            disabled={pending}
            onClick={onCancel}
            type="button"
          >
            Close
          </button>
        </div>
        <div className="grid gap-4 px-5 py-4 sm:px-6">
          {error ? (
            <p className="error" id="staff-lifecycle-error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="field">
            <label
              className="text-sm font-semibold text-slate-700"
              htmlFor="staff-lifecycle-reason"
            >
              Reason
            </label>
            <textarea
              ref={reasonRef}
              aria-describedby={error ? "staff-lifecycle-error" : undefined}
              aria-invalid={error ? true : undefined}
              className="input min-h-28"
              id="staff-lifecycle-reason"
              maxLength={500}
              minLength={3}
              name="reason"
              required
            />
            <span className="text-xs text-slate-500">
              Use 3 to 500 characters.
            </span>
          </div>
          <div>
            <button
              className="button button-cancel"
              disabled={pending}
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className={
                action === "suspend"
                  ? "button button-warn"
                  : "button button-success"
              }
              disabled={pending}
              type="submit"
            >
              {pending ? "Saving…" : confirmLabel}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
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
  const [inviting, setInviting] = useState(false);
  const [editingProfile, setEditingProfile] = useState(false);
  const [editingRoles, setEditingRoles] = useState(false);
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
      setEditingRoles(false);
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

  function closeInvite() {
    if (pending) return;
    setInviting(false);
    setInviteErrors({});
    setError(null);
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
      setInviting(false);
      setInviteErrors({});
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
      setEditingProfile(false);
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

  function closeDetails() {
    if (pending) return;
    detailRequest.current += 1;
    setDetailLoading(false);
    setSelected(null);
    setEditingProfile(false);
    setEditingRoles(false);
    setProfileErrors({});
    setAssignmentError(null);
    setAssignmentMessage(null);
    setAssignedRoles(null);
  }

  const locked = selected
    ? selected.status === "TERMINATED" || selected.status === "ARCHIVED"
    : false;
  const shownCount = `${staff.length} ${staff.length === 1 ? "person" : "people"}`;

  return (
    <div className="grid gap-5">
      {allowMutations && !canViewRoles ? (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          Inviting staff requires permission to view roles.
        </p>
      ) : null}
      {!inviting && !editingProfile && !editingRoles && !lifecycle && error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {success ? (
        <p
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
          role="status"
        >
          {success}
        </p>
      ) : null}

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(22rem,0.85fr)]">
        <section
          aria-labelledby="staff-directory-title"
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        >
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-4 sm:px-5">
            <div>
              <h2
                className="text-lg font-bold text-slate-900"
                id="staff-directory-title"
              >
                Directory
              </h2>
              <p className="mt-0.5 text-sm text-slate-500">
                {loading && staff.length === 0
                  ? "Fetching the latest records."
                  : `${shownCount} shown`}
              </p>
            </div>
            {allowInvite ? (
              <button
                className="button button-create"
                onClick={() => setInviting(true)}
                type="button"
              >
                Invite staff
              </button>
            ) : null}
          </div>
          <form
            className="border-b border-slate-100 bg-slate-50/80 p-4 sm:p-5"
            onSubmit={(event) => void applyFilters(event)}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="field sm:col-span-2">
                <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                  Search staff
                </span>
                <input
                  className="input"
                  maxLength={100}
                  name="search"
                  placeholder="Name, email, or code"
                  type="search"
                />
              </label>
              <label className="field">
                <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                  Profile status
                </span>
                <select className="input" defaultValue="" name="status">
                  <option value="">All</option>
                  {PROFILE_STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {statusLabel(status)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                  Membership status
                </span>
                <select
                  className="input"
                  defaultValue=""
                  name="membership_status"
                >
                  <option value="">All</option>
                  {MEMBERSHIP_STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {statusLabel(status)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="mt-3 flex justify-end">
              <button className="button" type="submit">
                Apply filters
              </button>
            </div>
          </form>
          {loading && staff.length === 0 ? (
            <div className="grid gap-3 p-5">
              <p className="text-sm font-medium text-slate-500" role="status">
                Loading staff.
              </p>
              {[0, 1, 2].map((item) => (
                <div
                  className="h-16 animate-pulse rounded-xl bg-slate-100"
                  key={item}
                />
              ))}
            </div>
          ) : null}
          {!loading && staff.length === 0 && !error && !success ? (
            <div className="px-6 py-16 text-center" role="status">
              <p className="font-semibold text-slate-800">
                No staff records match this view.
              </p>
              <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
                Try another name, email, or status.
              </p>
            </div>
          ) : null}

          {staff.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {staff.map((person) => {
                const active = selected?.id === person.id;
                return (
                  <li key={person.id}>
                    <button
                      aria-label={`View details for ${person.display_name}`}
                      aria-pressed={active}
                      className={`flex w-full cursor-pointer items-center gap-3 border-0 border-l-4 px-4 py-3.5 text-left font-[inherit] transition sm:px-5 ${
                        active
                          ? "border-emerald-600 bg-emerald-50/80"
                          : "border-transparent bg-white hover:bg-slate-50"
                      }`}
                      onClick={() => void openDetails(person.id)}
                      type="button"
                    >
                      <span
                        aria-hidden="true"
                        className={`grid size-11 shrink-0 place-items-center rounded-xl text-sm font-black ${
                          active
                            ? "bg-emerald-600 text-white"
                            : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {initials(person.display_name)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-semibold text-slate-900">
                          {person.display_name}
                        </span>
                        <span className="mt-0.5 block truncate text-sm text-slate-500">
                          {person.membership.email}
                        </span>
                        <span className="mt-0.5 block text-xs font-medium text-slate-400">
                          {person.staff_code}
                        </span>
                        <span className="mt-2 flex flex-wrap gap-2 sm:hidden">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="text-[0.62rem] font-bold tracking-wide text-slate-400 uppercase">
                              Profile
                            </span>
                            <StatusBadge status={person.status} />
                          </span>
                          <span className="inline-flex items-center gap-1.5">
                            <span className="text-[0.62rem] font-bold tracking-wide text-slate-400 uppercase">
                              Access
                            </span>
                            <StatusBadge status={person.membership.status} />
                          </span>
                        </span>
                      </span>
                      <span className="hidden shrink-0 items-center gap-2 sm:flex">
                        <span className="text-right">
                          <span className="mb-1 block text-[0.62rem] font-bold tracking-wide text-slate-400 uppercase">
                            Profile
                          </span>
                          <StatusBadge status={person.status} />
                        </span>
                        <span className="text-right">
                          <span className="mb-1 block text-[0.62rem] font-bold tracking-wide text-slate-400 uppercase">
                            Access
                          </span>
                          <StatusBadge status={person.membership.status} />
                        </span>
                      </span>
                      <span
                        aria-hidden="true"
                        className={`text-lg ${active ? "text-emerald-600" : "text-slate-300"}`}
                      >
                        ›
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {nextAfter ? (
            <div className="border-t border-slate-100 p-4 text-center">
              <button
                className="button"
                disabled={paging || pending}
                onClick={() => void loadMore()}
                type="button"
              >
                {paging ? "Loading more…" : "Load more"}
              </button>
            </div>
          ) : null}
        </section>

        <div className="xl:sticky xl:top-20">
          {detailLoading ? (
            <p className="mb-3 text-sm text-slate-500" role="status">
              Loading staff details.
            </p>
          ) : null}
          {!selected && !detailLoading ? (
            <div className="grid min-h-72 place-items-center rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center shadow-sm">
              <div>
                <p className="font-semibold text-slate-800">
                  No person selected
                </p>
                <p className="mx-auto mt-1 max-w-xs text-sm leading-6 text-slate-500">
                  Choose someone from the directory to review their profile,
                  roles, and access.
                </p>
              </div>
            </div>
          ) : null}
          {selected ? (
            <section
              aria-labelledby="staff-detail-title"
              className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
              role="region"
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden="true"
                  className="grid size-12 shrink-0 place-items-center rounded-2xl bg-emerald-600 text-sm font-black text-white"
                >
                  {initials(selected.display_name)}
                </span>
                <div className="min-w-0 flex-1">
                  <h2
                    className="truncate text-xl font-bold text-slate-900"
                    id="staff-detail-title"
                  >
                    {selected.display_name}
                  </h2>
                  <p className="mt-0.5 text-sm text-slate-500">
                    {selected.staff_code}
                  </p>
                </div>
                <button
                  className="button button-cancel button-sm"
                  onClick={closeDetails}
                  type="button"
                >
                  Close
                </button>
              </div>
              <div className="mt-4 flex flex-wrap gap-4">
                <div>
                  <p className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
                    Profile
                  </p>
                  <div className="mt-1">
                    <StatusBadge status={selected.status} />
                  </div>
                </div>
                <div>
                  <p className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
                    Access
                  </p>
                  <div className="mt-1">
                    <StatusBadge status={selected.membership.status} />
                  </div>
                </div>
              </div>
              <ProfileFacts staff={selected} />
              {locked ? (
                <p className="mt-4 rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-600">
                  Login access stays as it is while this profile is{" "}
                  {statusLabel(selected.status).toLowerCase()}.
                </p>
              ) : null}
              {allowMutations &&
              (!editingProfile ||
                (!locked &&
                  (selected.membership.status === "ACTIVE" ||
                    selected.membership.status === "SUSPENDED"))) ? (
                <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                  {allowMutations && !editingProfile ? (
                    <button
                      className="button button-edit"
                      onClick={() => setEditingProfile(true)}
                      type="button"
                    >
                      Edit profile
                    </button>
                  ) : null}
                  {allowMutations &&
                  !locked &&
                  selected.membership.status === "ACTIVE" ? (
                    <button
                      className="button button-warn"
                      disabled={pending}
                      onClick={() => {
                        setLifecycleError(null);
                        setLifecycle({ staff: selected, action: "suspend" });
                      }}
                      type="button"
                    >
                      Suspend access
                    </button>
                  ) : null}
                  {allowMutations &&
                  !locked &&
                  selected.membership.status === "SUSPENDED" ? (
                    <button
                      className="button button-success"
                      disabled={pending}
                      onClick={() => {
                        setLifecycleError(null);
                        setLifecycle({ staff: selected, action: "activate" });
                      }}
                      type="button"
                    >
                      Activate access
                    </button>
                  ) : null}
                </div>
              ) : null}
              {allowMutations && editingProfile ? (
                <FormDialog
                  description={`Staff code ${selected.staff_code}`}
                  key={`${selected.id}:${selected.staff_code}:${selected.display_name}:${selected.phone}:${selected.joined_date}:${selected.employment_title}:${selected.default_commission_percent}`}
                  noValidate
                  onClose={() => {
                    if (!pending) setEditingProfile(false);
                  }}
                  onSubmit={(event) => void submitProfile(event)}
                  pending={pending}
                  title="Edit profile"
                >
                  <div className="grid gap-3 md:grid-cols-2">
                    {error ? (
                      <p className="error md:col-span-2" role="alert">
                        {error}
                      </p>
                    ) : null}
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
                    <div className="md:col-span-2">
                      <button
                        className="button button-cancel"
                        disabled={pending}
                        onClick={() => setEditingProfile(false)}
                        type="button"
                      >
                        Cancel
                      </button>
                      <button
                        className="button button-edit"
                        disabled={pending}
                        type="submit"
                      >
                        {pending ? "Saving…" : "Save staff profile"}
                      </button>
                    </div>
                  </div>
                </FormDialog>
              ) : null}
              {allowMutations && !canViewRoles ? (
                <p className="mt-4 rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                  Assigning roles requires permission to view roles.
                </p>
              ) : null}
              {allowInvite ? (
                <section
                  aria-labelledby="assign-roles-title"
                  className="mt-6 rounded-2xl bg-slate-50 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3
                        className="text-base font-bold text-slate-900"
                        id="assign-roles-title"
                      >
                        Assign roles
                      </h3>
                      <p className="mt-1 text-sm text-slate-500">
                        Saving replaces the roles this person can use.
                      </p>
                    </div>
                    {rolesLoaded &&
                    !rolesError &&
                    !assignmentBlocked &&
                    offeredRoles.length > 0 &&
                    assignedRoles &&
                    !editingRoles ? (
                      <button
                        className="button button-edit button-sm"
                        onClick={() => setEditingRoles(true)}
                        type="button"
                      >
                        Edit assigned roles
                      </button>
                    ) : null}
                  </div>
                  {assignmentLoading || !rolesLoaded ? (
                    <p className="mt-3 text-sm text-slate-500" role="status">
                      Loading assigned roles.
                    </p>
                  ) : null}
                  {assignmentError && !editingRoles ? (
                    <p className="error mt-3" role="alert">
                      {assignmentError}
                    </p>
                  ) : null}
                  {assignmentMessage ? (
                    <p
                      className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-900"
                      role="status"
                    >
                      {assignmentMessage}
                    </p>
                  ) : null}
                  {rolesError ? (
                    <p className="error mt-3" role="alert">
                      {rolesError}
                    </p>
                  ) : null}
                  {assignedRoles ? (
                    <p className="mt-3 text-sm text-slate-500">
                      Authorization version {assignedRoles.authz_version}
                    </p>
                  ) : null}
                  {assignedRoles &&
                  !assignmentBlocked &&
                  assignedRoles.roles.every((role) => role.key === "OWNER") ? (
                    <p className="mt-3 text-sm text-slate-500">
                      No roles are assigned.
                    </p>
                  ) : null}
                  {assignedRoles &&
                  assignedRoles.roles.filter((role) => role.key !== "OWNER")
                    .length > 0 ? (
                    <ul aria-label="Current roles" className="mt-3 grid gap-2">
                      {assignedRoles.roles
                        .filter((role) => role.key !== "OWNER")
                        .map((role) => {
                          const retained = !offeredRoles.some(
                            (offered) => offered.id === role.id,
                          );
                          return (
                            <li key={role.id}>
                              {retained ? (
                                <RoleOption
                                  defaultChecked
                                  disabled
                                  note="Outside your delegation boundary."
                                  role={role}
                                />
                              ) : (
                                <span className="inline-flex rounded-full bg-white px-3 py-1 text-sm font-semibold text-slate-800 ring-1 ring-slate-200">
                                  {role.name}
                                </span>
                              )}
                            </li>
                          );
                        })}
                    </ul>
                  ) : null}
                  {rolesLoaded && !rolesError && assignmentBlocked ? (
                    <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                      {OWNER_REQUIRED}
                    </p>
                  ) : null}
                  {rolesLoaded &&
                  !rolesError &&
                  !assignmentBlocked &&
                  offeredRoles.length === 0 ? (
                    <p className="mt-3 text-sm text-slate-500">
                      No assignable roles are available.
                    </p>
                  ) : null}
                  {rolesLoaded &&
                  !rolesError &&
                  !assignmentBlocked &&
                  offeredRoles.length > 0 &&
                  assignedRoles &&
                  editingRoles ? (
                    <FormDialog
                      description="Choose every role this person should keep."
                      key={`${selected.membership.gym_membership_id}:${assignedRoles.authz_version}:${assignedRoles.roles.map((role) => role.id).join(",")}`}
                      noValidate
                      onClose={() => {
                        if (!pending) setEditingRoles(false);
                      }}
                      onSubmit={(event) => void submitAssignment(event)}
                      pending={pending}
                      title="Assigned roles"
                    >
                      {assignmentError ? (
                        <p className="error" role="alert">
                          {assignmentError}
                        </p>
                      ) : null}
                      <fieldset className="grid gap-2">
                        <legend className="text-sm font-semibold text-slate-700">
                          Role choices
                        </legend>
                        {offeredRoles.map((role) => (
                          <RoleOption
                            defaultChecked={assignedRoles.roles.some(
                              (assigned) => assigned.id === role.id,
                            )}
                            key={role.id}
                            role={role}
                          />
                        ))}
                      </fieldset>
                      <div>
                        <button
                          className="button button-cancel"
                          disabled={pending}
                          onClick={() => setEditingRoles(false)}
                          type="button"
                        >
                          Cancel
                        </button>
                        <button
                          className="button button-edit"
                          disabled={pending}
                          type="submit"
                        >
                          {pending ? "Saving…" : "Save assigned roles"}
                        </button>
                      </div>
                    </FormDialog>
                  ) : null}
                </section>
              ) : null}
            </section>
          ) : null}
        </div>
      </div>

      {allowInvite && inviting ? (
        <div
          className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
          onMouseDown={(event) => {
            if (event.target !== event.currentTarget) return;
            closeInvite();
          }}
          role="presentation"
        >
          <form
            aria-describedby="invite-staff-description"
            aria-labelledby="invite-staff-title"
            aria-modal="true"
            className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-3xl"
            noValidate
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              closeInvite();
            }}
            onSubmit={(event) => void submitInvite(event)}
            role="dialog"
            tabIndex={-1}
          >
            <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <div>
                <h2 className="text-xl font-bold" id="invite-staff-title">
                  Invite staff
                </h2>
                <p
                  className="mt-1 text-sm text-slate-500"
                  id="invite-staff-description"
                >
                  A staff code is assigned from the display name.
                </p>
              </div>
              <button
                className="button button-cancel button-sm"
                disabled={pending}
                onClick={closeInvite}
                type="button"
              >
                Close
              </button>
            </div>
            {!rolesLoaded ||
            rolesError ||
            error ||
            (rolesLoaded && !rolesError && offeredRoles.length === 0) ? (
              <div className="grid gap-3 px-5 py-4 sm:px-6">
                {!rolesLoaded ? <p role="status">Loading roles.</p> : null}
                {rolesError ? (
                  <p className="error" role="alert">
                    {rolesError}
                  </p>
                ) : null}
                {rolesLoaded && !rolesError && offeredRoles.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    No assignable roles are available.
                  </p>
                ) : null}
                {error ? (
                  <p className="error" role="alert">
                    {error}
                  </p>
                ) : null}
              </div>
            ) : null}
            {rolesLoaded && !rolesError && offeredRoles.length > 0 ? (
              <div className="grid gap-3 px-5 py-4 md:grid-cols-2 sm:px-6">
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
                  <legend className="text-sm font-semibold text-slate-700">
                    Roles
                  </legend>
                  <p className="text-sm text-slate-500">
                    Choose at least one role. Owner is not offered here.
                  </p>
                  {offeredRoles.map((role) => (
                    <RoleOption key={role.id} role={role} />
                  ))}
                  {inviteErrors.role_ids ? (
                    <p className="error">{inviteErrors.role_ids}</p>
                  ) : null}
                </fieldset>
                <div className="sticky bottom-0 z-10 mt-2 flex flex-wrap justify-end gap-2 border-t border-slate-100 bg-white py-4 md:col-span-2">
                  <button
                    className="button button-cancel"
                    disabled={pending}
                    onClick={closeInvite}
                    type="button"
                  >
                    Cancel
                  </button>
                  <button
                    className="button button-create"
                    disabled={pending}
                    type="submit"
                  >
                    {pending ? "Sending…" : "Send invitation"}
                  </button>
                </div>
              </div>
            ) : null}
          </form>
        </div>
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

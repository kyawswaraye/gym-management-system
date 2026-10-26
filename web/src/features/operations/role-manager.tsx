"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { FormDialog } from "@/components/form-dialog";
import {
  archiveRole,
  createRole,
  listAllPermissions,
  listAllRoles,
  replaceRolePermissions,
  updateRole,
} from "@/features/operations/client";
import {
  canMutateCustomRole,
  catalogAvailability,
  isCustomRole,
  permissionOptions,
  type PermissionOption,
} from "@/features/operations/role-policy";
import type { Permission, Role, RoleCreate, RoleUpdate } from "@/types/api";

type FieldErrors = Record<string, string>;
type IdentityValue = {
  name: string;
  description: string;
  is_active?: boolean;
};
type IdentityResult =
  | { ok: true; value: IdentityValue }
  | { ok: false; errors: FieldErrors; message: string };
type RoleKind = "all" | "protected" | "custom" | "inactive";

const PERMISSION_PREVIEW = 6;
const panelClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const PROTECTED_ORDER = [
  "OWNER",
  "MANAGER",
  "RECEPTIONIST",
  "ACCOUNTANT",
  "TRAINER",
];
const MODULE_LABELS: Record<string, string> = {
  audit: "Audit",
  cashbook: "Cash book",
  checkins: "Check-ins",
  expenses: "Expenses",
  files: "Files",
  gym: "Gym",
  members: "Members",
  memberships: "Memberships",
  notifications: "Notifications",
  payments: "Payments",
  platform: "Platform",
  pt: "Personal training",
  receipts: "Receipts",
  reports: "Reports",
  roles: "Roles",
  salaries: "Salaries",
  settings: "Settings",
  staff: "Staff",
};

function failureMessage(failure: unknown, fallback: string): string {
  return failure instanceof Error && failure.message
    ? failure.message
    : fallback;
}

function readIdentity(form: FormData, includeActive: boolean): IdentityResult {
  const name = String(form.get("name") ?? "").trim();
  const description = String(form.get("description") ?? "").trim();
  const errors: FieldErrors = {};
  if (!name || name.length > 100) {
    errors.name = "Enter a role name up to 100 characters.";
  }
  if (description.length > 255) {
    errors.description = "Enter a description up to 255 characters.";
  }
  if (Object.keys(errors).length > 0) {
    return {
      ok: false,
      errors,
      message: Object.values(errors)[0] ?? "Check the role.",
    };
  }
  const value: IdentityValue = { name, description };
  if (includeActive) value.is_active = form.get("is_active") === "on";
  return { ok: true, value };
}

function moduleOf(code: string): string {
  return code.split(".")[0] || "other";
}

function moduleLabel(module: string): string {
  return (
    MODULE_LABELS[module] ?? module.charAt(0).toUpperCase() + module.slice(1)
  );
}

function groupCodes(codes: readonly string[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const code of [...codes].sort()) {
    const key = moduleOf(code);
    const list = groups.get(key);
    if (list) list.push(code);
    else groups.set(key, [code]);
  }
  return [...groups.entries()].sort((a, b) =>
    moduleLabel(a[0]).localeCompare(moduleLabel(b[0])),
  );
}

function groupOptions(
  options: readonly PermissionOption[],
): [string, PermissionOption[]][] {
  const groups = new Map<string, PermissionOption[]>();
  for (const option of [...options].sort((a, b) =>
    a.code.localeCompare(b.code),
  )) {
    const key = moduleOf(option.code);
    const list = groups.get(key);
    if (list) list.push(option);
    else groups.set(key, [option]);
  }
  return [...groups.entries()].sort((a, b) =>
    moduleLabel(a[0]).localeCompare(moduleLabel(b[0])),
  );
}

function groupPermissions(
  permissions: readonly Permission[],
): [string, Permission[]][] {
  const groups = new Map<string, Permission[]>();
  for (const permission of [...permissions].sort((a, b) =>
    a.code.localeCompare(b.code),
  )) {
    const list = groups.get(permission.module);
    if (list) list.push(permission);
    else groups.set(permission.module, [permission]);
  }
  return [...groups.entries()].sort((a, b) =>
    moduleLabel(a[0]).localeCompare(moduleLabel(b[0])),
  );
}

function compareRoles(a: Role, b: Role): number {
  if (a.is_protected !== b.is_protected) return a.is_protected ? -1 : 1;
  if (a.is_protected && b.is_protected) {
    const left = PROTECTED_ORDER.indexOf(a.key);
    const right = PROTECTED_ORDER.indexOf(b.key);
    const order = (left === -1 ? 99 : left) - (right === -1 ? 99 : right);
    if (order !== 0) return order;
  }
  if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
  return a.name.localeCompare(b.name);
}

function permissionDetail(permission: Permission): string {
  const description = permission.description.trim();
  if (!description || description === permission.code) return "";
  return description;
}

function optionMatches(option: PermissionOption, query: string): boolean {
  if (!query) return true;
  return `${option.code} ${option.description} ${option.reason} ${moduleLabel(moduleOf(option.code))}`
    .toLowerCase()
    .includes(query);
}

function tabClass(selected: boolean): string {
  return selected
    ? "min-h-10 rounded-lg bg-white px-4 text-sm font-bold text-slate-900 shadow-sm"
    : "min-h-10 rounded-lg px-4 text-sm font-bold text-slate-500 hover:text-slate-800";
}

function filterClass(selected: boolean): string {
  return selected
    ? "inline-flex min-h-9 items-center rounded-full bg-slate-900 px-3 text-sm font-semibold text-white"
    : "inline-flex min-h-9 items-center rounded-full bg-slate-100 px-3 text-sm font-semibold text-slate-600 ring-1 ring-slate-200 ring-inset hover:bg-slate-200";
}

const ROLE_BRIEFS: Record<string, string> = {
  OWNER: "Owns the gym. Settings and final control stay here.",
  MANAGER: "Runs the floor, staff, and day-to-day work.",
  RECEPTIONIST: "Front desk for members, check-ins, and payments.",
  ACCOUNTANT: "Payments, expenses, salaries, and reports.",
  TRAINER: "Coaching, sessions, and personal training.",
};

function roleBrief(role: Role): string {
  if (role.is_protected) {
    const brief = ROLE_BRIEFS[role.key];
    if (brief) return brief;
  }
  return role.description.trim() || "Custom role for this gym.";
}

function Field({
  label,
  name,
  error,
  defaultValue = "",
  maxLength,
  required = false,
  placeholder,
}: {
  label: string;
  name: string;
  error?: string | undefined;
  defaultValue?: string;
  maxLength?: number;
  required?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label className="text-sm font-semibold text-slate-700" htmlFor={id}>
        {label}
      </label>
      <input
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        autoComplete="off"
        className="input"
        defaultValue={defaultValue}
        id={id}
        maxLength={maxLength}
        name={name}
        placeholder={placeholder}
        required={required}
      />
      {error ? (
        <span className="error" id={errorId}>
          {error}
        </span>
      ) : null}
    </div>
  );
}

function Pill({
  children,
  tone,
}: {
  children: string;
  tone: "amber" | "emerald" | "slate" | "violet";
}) {
  const toneClass = {
    amber: "bg-amber-50 text-amber-800 ring-amber-600/15",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-600/15",
    slate: "bg-slate-100 text-slate-600 ring-slate-500/10",
    violet: "bg-violet-50 text-violet-700 ring-violet-600/15",
  }[tone];
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold whitespace-nowrap ring-1 ring-inset ${toneClass}`}
    >
      {children}
    </span>
  );
}

function ArchiveDialog({
  roleName,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  roleName: string;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const title = `Archive ${roleName}`;

  useEffect(() => {
    panelRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget || pending) return;
        onCancel();
      }}
      role="presentation"
    >
      <div
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,32rem)] sm:max-w-lg"
        onKeyDown={(event) => {
          if (event.key !== "Escape" || pending) return;
          event.preventDefault();
          onCancel();
        }}
        ref={panelRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {title}
            </h2>
            <p
              className="mt-1 text-sm leading-6 text-slate-500"
              id={descriptionId}
            >
              {roleName} becomes inactive. Assignments stay on record, and the
              role can be restored later.
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
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
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
              className="button button-archive"
              disabled={pending}
              onClick={onConfirm}
              type="button"
            >
              {pending ? "Archiving…" : "Confirm archive"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function PermissionChoice({
  option,
  checked,
}: {
  option: PermissionOption;
  checked: boolean;
}) {
  const reasonId = useId();
  if (option.mode === "unavailable") {
    return (
      <>
        <p className="font-mono text-sm font-semibold break-all text-slate-700">
          {option.code}
        </p>
        {option.description ? (
          <p className="mt-1 text-sm leading-5 text-slate-500">
            {option.description}
          </p>
        ) : null}
        {option.reason ? (
          <p className="mt-1 text-sm leading-5 text-amber-800">
            {option.reason}
          </p>
        ) : null}
      </>
    );
  }
  return (
    <>
      <div className="flex items-start gap-3 px-3 py-3">
        <label
          className={`flex min-w-0 flex-1 items-start gap-3 font-mono text-sm font-semibold break-all text-slate-900 ${
            option.mode === "required" ? "cursor-not-allowed" : "cursor-pointer"
          }`}
        >
          <input
            aria-describedby={option.reason ? reasonId : undefined}
            className="mt-0.5 size-4 shrink-0 accent-emerald-700"
            defaultChecked={checked}
            disabled={option.mode === "required"}
            name="permission_codes"
            type="checkbox"
            value={option.code}
          />
          {option.code}
        </label>
        {option.mode === "required" ? (
          <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase ring-1 ring-slate-200 ring-inset">
            Required
          </span>
        ) : null}
      </div>
      {option.description ? (
        <p
          className={`px-3 text-sm leading-5 text-slate-500 ${option.reason ? "" : "pb-3"}`}
        >
          {option.description}
        </p>
      ) : null}
      {option.reason ? (
        <p
          className="mt-1 px-3 pb-3 text-sm leading-5 text-amber-800"
          id={reasonId}
        >
          {option.reason}
        </p>
      ) : null}
    </>
  );
}

function choiceClass(option: PermissionOption, shown: boolean): string {
  if (!shown) return "hidden";
  if (option.mode === "unavailable") return "rounded-xl bg-slate-50 px-3 py-3";
  if (option.mode === "required") {
    return "rounded-xl border border-slate-200 bg-slate-50";
  }
  return "rounded-xl border border-slate-200 bg-white has-[:checked]:border-emerald-300 has-[:checked]:bg-emerald-50/80";
}

function PermissionEditor({
  role,
  options,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  role: Role;
  options: PermissionOption[];
  pending: boolean;
  error: string | null;
  onSubmit: (event: FormEvent<HTMLFormElement>, role: Role) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const kept = new Set(
    options
      .filter((option) => option.mode !== "unavailable")
      .map((option) => option.code),
  );
  const dropped = role.permission_codes.filter((code) => !kept.has(code));
  const required = options.filter((option) => option.mode === "required");
  const unavailable = options.filter((option) => option.mode === "unavailable");
  const editable = options.filter((option) => option.mode === "editable");
  const anyMatch = options.some((option) => optionMatches(option, normalized));
  const checked = (option: PermissionOption) =>
    option.mode === "required" || role.permission_codes.includes(option.code);

  return (
    <FormDialog
      description="Choose what this role is allowed to do."
      noValidate
      onClose={onClose}
      onSubmit={(event) => onSubmit(event, role)}
      pending={pending}
      title={`Permissions for ${role.name}`}
      wide
    >
      <label className="field">
        <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
          Filter
        </span>
        <input
          className="input"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Code or area"
          type="search"
          value={query}
        />
      </label>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <fieldset className="m-0 grid min-w-0 gap-5 border-0 p-0">
        <legend className="sr-only">Permissions for {role.name}</legend>
        {dropped.length > 0 ? (
          <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">
            Saving replaces the full permission set. Permissions outside the
            delegation policy are removed.
          </p>
        ) : null}
        {editable.length === 0 && required.length === 0 ? (
          <p className="text-sm text-slate-500">
            No permissions in the current delegation policy can be selected.
          </p>
        ) : null}
        {normalized && !anyMatch ? (
          <p className="text-sm text-slate-500">
            No permissions match this filter.
          </p>
        ) : null}
        {required.length > 0 ? (
          <div
            className={
              required.some((option) => optionMatches(option, normalized))
                ? "grid gap-2"
                : "hidden"
            }
          >
            <h3 className="text-xs font-bold tracking-wide text-slate-500 uppercase">
              Locked on this role
            </h3>
            <ul className="grid gap-2">
              {required.map((option) => (
                <li
                  className={choiceClass(
                    option,
                    optionMatches(option, normalized),
                  )}
                  key={option.code}
                >
                  <PermissionChoice checked={checked(option)} option={option} />
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {editable.length > 0 ? (
          <div
            className={
              editable.some((option) => optionMatches(option, normalized))
                ? "grid gap-4"
                : "hidden"
            }
          >
            <h3 className="text-xs font-bold tracking-wide text-slate-500 uppercase">
              You can change
            </h3>
            {groupOptions(editable).map(([module, items]) => (
              <div
                className={
                  items.some((option) => optionMatches(option, normalized))
                    ? "grid gap-2"
                    : "hidden"
                }
                key={module}
              >
                <h4 className="text-sm font-semibold text-slate-800">
                  {moduleLabel(module)}
                </h4>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {items.map((option) => (
                    <li
                      className={choiceClass(
                        option,
                        optionMatches(option, normalized),
                      )}
                      key={option.code}
                    >
                      <PermissionChoice
                        checked={checked(option)}
                        option={option}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : null}
        {unavailable.length > 0 ? (
          <div
            className={
              unavailable.some((option) => optionMatches(option, normalized))
                ? "grid gap-2"
                : "hidden"
            }
          >
            <h3 className="text-xs font-bold tracking-wide text-slate-500 uppercase">
              Not available
            </h3>
            <ul className="grid gap-2">
              {unavailable.map((option) => (
                <li
                  className={choiceClass(
                    option,
                    optionMatches(option, normalized),
                  )}
                  key={option.code}
                >
                  <PermissionChoice checked={false} option={option} />
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="sticky bottom-0 z-10 -mx-5 -mb-4 flex flex-wrap items-center justify-end gap-3 border-t border-slate-100 bg-white px-5 py-3 sm:-mx-6 sm:px-6">
          <button
            aria-label={
              pending
                ? "Saving permissions"
                : `Save permissions for ${role.name}`
            }
            className="button button-edit"
            disabled={pending}
            type="submit"
          >
            {pending ? "Saving…" : "Save permissions"}
          </button>
        </div>
      </fieldset>
    </FormDialog>
  );
}

function selectedPermissionCodes(
  form: FormData,
  options: readonly PermissionOption[],
): string[] {
  const allowed = new Set(
    options
      .filter((option) => option.mode === "editable")
      .map((option) => option.code),
  );
  const required = options
    .filter((option) => option.mode === "required")
    .map((option) => option.code);
  const selected = [
    ...new Set(
      form
        .getAll("permission_codes")
        .map(String)
        .filter((code) => allowed.has(code)),
    ),
  ];
  return [...required, ...selected.filter((code) => !required.includes(code))];
}

function RoleCard({
  role,
  customMutable,
  outsideBoundary,
  options,
  pending,
  editing,
  permissionsOpen,
  fieldErrors,
  errorScope,
  formError,
  onEdit,
  onCloseEdit,
  onSubmitIdentity,
  onArchive,
  onRestore,
  onOpenPermissions,
  onClosePermissions,
  onSubmitPermissions,
}: {
  role: Role;
  customMutable: boolean;
  outsideBoundary: boolean;
  options: PermissionOption[] | null;
  pending: boolean;
  editing: boolean;
  permissionsOpen: boolean;
  fieldErrors: FieldErrors;
  errorScope: string | null;
  formError: string | null;
  onEdit: () => void;
  onCloseEdit: () => void;
  onSubmitIdentity: (event: FormEvent<HTMLFormElement>) => void;
  onArchive: () => void;
  onRestore: () => void;
  onOpenPermissions: () => void;
  onClosePermissions: () => void;
  onSubmitPermissions: (event: FormEvent<HTMLFormElement>, role: Role) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const codes = [...role.permission_codes].sort();
  const compact = codes.length > PERMISSION_PREVIEW;
  const identityError = errorScope === role.id;
  const brief = roleBrief(role);

  return (
    <article
      aria-labelledby={`role-${role.id}`}
      className="rounded-2xl border border-slate-200 bg-white p-5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h3
            className="text-xl font-bold text-slate-900"
            id={`role-${role.id}`}
          >
            {role.name}
          </h3>
          <p className="mt-1 text-sm text-slate-500">{role.key}</p>
          <p className="mt-2 text-sm leading-6 text-slate-600">{brief}</p>
        </div>
        {customMutable || options ? (
          <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
            {options ? (
              <button
                aria-label={`Edit permissions for ${role.name}`}
                className="button button-sm"
                disabled={pending}
                onClick={onOpenPermissions}
                type="button"
              >
                Permissions
              </button>
            ) : null}
            {customMutable ? (
              <button
                aria-label={`Edit ${role.name}`}
                className="button button-edit button-sm"
                disabled={pending}
                onClick={onEdit}
                type="button"
              >
                Edit
              </button>
            ) : null}
            {customMutable && role.is_active ? (
              <button
                aria-label={`Archive ${role.name}`}
                className="button button-archive button-sm"
                disabled={pending}
                onClick={onArchive}
                type="button"
              >
                Archive
              </button>
            ) : null}
            {customMutable && !role.is_active ? (
              <button
                aria-label={`Restore ${role.name}`}
                className="button button-success button-sm"
                disabled={pending}
                onClick={onRestore}
                type="button"
              >
                Restore
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <dl className="mt-4 grid gap-3 border-t border-slate-100 pt-4 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Status</dt>
          <dd className="mt-1 font-semibold text-slate-900">
            {role.is_protected ? "Protected" : "Custom"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Activity</dt>
          <dd className="mt-1 font-semibold text-slate-900">
            {role.is_active ? "Active" : "Inactive"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Assigned</dt>
          <dd className="mt-1 font-semibold text-slate-900">
            {role.assignment_count}
          </dd>
        </div>
      </dl>

      {outsideBoundary ? (
        <p className="mt-3 text-sm text-slate-600">
          This role is outside your delegation boundary.
        </p>
      ) : null}

      <div aria-label={`Permissions on ${role.name}`} className="mt-4">
        {codes.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No permissions assigned.
          </p>
        ) : null}
        {codes.length > 0 && !compact ? (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {codes.map((code) => (
              <li
                className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-600"
                key={code}
              >
                {code}
              </li>
            ))}
          </ul>
        ) : null}
        {compact ? (
          <button
            aria-expanded={expanded}
            className="mt-1 inline-flex min-h-8 items-center text-sm font-semibold text-slate-600"
            onClick={() => setExpanded((current) => !current)}
            type="button"
          >
            {expanded ? "Hide permissions" : "Show permissions"}
          </button>
        ) : null}
        {compact && expanded ? (
          <div className="mt-2 grid gap-3 border-t border-slate-100 pt-3">
            {groupCodes(codes).map(([module, moduleCodes]) => (
              <div key={module}>
                <p className="text-[0.68rem] font-bold tracking-wide text-slate-400 uppercase">
                  {moduleLabel(module)}
                </p>
                <ul className="mt-1.5 flex flex-wrap gap-1.5">
                  {moduleCodes.map((code) => (
                    <li
                      className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-600"
                      key={code}
                    >
                      {code}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {customMutable && editing ? (
        <FormDialog
          description={`Key ${role.key} stays the same.`}
          key={`${role.id}:${role.updated_at}:identity`}
          noValidate
          onClose={onCloseEdit}
          onSubmit={onSubmitIdentity}
          pending={pending}
          title={`Edit ${role.name}`}
        >
          <div className="grid gap-4">
            {formError ? (
              <p className="error" role="alert">
                {formError}
              </p>
            ) : null}
            <Field
              defaultValue={role.name}
              error={identityError ? fieldErrors.name : undefined}
              label="Name"
              maxLength={100}
              name="name"
              required
            />
            <Field
              defaultValue={role.description}
              error={identityError ? fieldErrors.description : undefined}
              label="Description"
              maxLength={255}
              name="description"
            />
            <div>
              <button
                aria-label={`Save ${role.name}`}
                className="button button-edit"
                disabled={pending}
                type="submit"
              >
                {pending ? "Saving…" : "Save changes"}
              </button>
            </div>
          </div>
        </FormDialog>
      ) : null}
      {options && permissionsOpen ? (
        <PermissionEditor
          error={formError}
          key={`${role.id}:${role.updated_at}:${role.permission_codes.join(",")}`}
          onClose={onClosePermissions}
          onSubmit={onSubmitPermissions}
          options={options}
          pending={pending}
          role={role}
        />
      ) : null}
    </article>
  );
}

export function RoleManager({
  canManage,
  isOwner,
  permissions,
}: {
  canManage: boolean;
  isOwner: boolean;
  permissions: readonly string[];
}) {
  const [roles, setRoles] = useState<Role[] | null>(null);
  const [section, setSection] = useState<"roles" | "catalog">("roles");
  const [catalog, setCatalog] = useState<Permission[] | null>(null);
  const [rolesError, setRolesError] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [errorScope, setErrorScope] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [creatingRole, setCreatingRole] = useState(false);
  const [editingRoleId, setEditingRoleId] = useState<string | null>(null);
  const [permissionsRoleId, setPermissionsRoleId] = useState<string | null>(
    null,
  );
  const [archiveTarget, setArchiveTarget] = useState<Role | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [roleQuery, setRoleQuery] = useState("");
  const [roleKind, setRoleKind] = useState<RoleKind>("all");
  const [catalogQuery, setCatalogQuery] = useState("");

  useEffect(() => {
    let active = true;
    void listAllRoles()
      .then((rows) => {
        if (active) setRoles(rows);
      })
      .catch((failure: unknown) => {
        if (!active) return;
        setRolesError(failureMessage(failure, "Unable to load roles."));
        setRoles([]);
      });
    void listAllPermissions()
      .then((rows) => {
        if (active) setCatalog(rows);
      })
      .catch((failure: unknown) => {
        if (!active) return;
        setCatalogError(failureMessage(failure, "Unable to load permissions."));
        setCatalog([]);
      });
    return () => {
      active = false;
    };
  }, []);

  async function reloadRoles(): Promise<void> {
    const rows = await listAllRoles();
    setRoles(rows);
    setRolesError(null);
  }

  function resetFeedback() {
    setFormError(null);
    setFieldErrors({});
    setErrorScope(null);
    setSuccess(null);
  }

  function openCreate() {
    setCreatingRole(true);
    setEditingRoleId(null);
    setPermissionsRoleId(null);
    resetFeedback();
  }

  function openEdit(roleId: string) {
    setEditingRoleId(roleId);
    setCreatingRole(false);
    setPermissionsRoleId(null);
    resetFeedback();
  }

  function openPermissions(roleId: string) {
    setPermissionsRoleId(roleId);
    setCreatingRole(false);
    setEditingRoleId(null);
    resetFeedback();
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = readIdentity(new FormData(form), true);
    if (!parsed.ok) {
      setErrorScope("create");
      setFieldErrors(parsed.errors);
      setFormError(parsed.message);
      setSuccess(null);
      return;
    }
    const body: RoleCreate = {
      name: parsed.value.name,
    };
    if (parsed.value.description) body.description = parsed.value.description;
    if (typeof parsed.value.is_active === "boolean") {
      body.is_active = parsed.value.is_active;
    }
    setPending(true);
    setErrorScope(null);
    setFieldErrors({});
    setFormError(null);
    setSuccess(null);
    try {
      await createRole(body);
      form.reset();
      setCreatingRole(false);
      await reloadRoles();
      setSuccess("Role created.");
    } catch (failure) {
      setFormError(failureMessage(failure, "Unable to create the role."));
    } finally {
      setPending(false);
    }
  }

  async function submitIdentity(event: FormEvent<HTMLFormElement>, role: Role) {
    event.preventDefault();
    const parsed = readIdentity(new FormData(event.currentTarget), false);
    if (!parsed.ok) {
      setErrorScope(role.id);
      setFieldErrors(parsed.errors);
      setFormError(parsed.message);
      setSuccess(null);
      return;
    }
    const body: RoleUpdate = {
      name: parsed.value.name,
      description: parsed.value.description,
    };
    setPending(true);
    setErrorScope(null);
    setFieldErrors({});
    setFormError(null);
    setSuccess(null);
    try {
      await updateRole(role.id, body);
      setEditingRoleId(null);
      await reloadRoles();
      setSuccess(`${role.name} updated.`);
    } catch (failure) {
      setFormError(failureMessage(failure, "Unable to update the role."));
    } finally {
      setPending(false);
    }
  }

  async function submitPermissions(
    event: FormEvent<HTMLFormElement>,
    role: Role,
    options: readonly PermissionOption[],
  ) {
    event.preventDefault();
    const codes = selectedPermissionCodes(
      new FormData(event.currentTarget),
      options,
    );
    setPending(true);
    setFormError(null);
    setSuccess(null);
    try {
      await replaceRolePermissions(role.id, codes);
      setPermissionsRoleId(null);
      await reloadRoles();
      setSuccess(`${role.name} permissions replaced.`);
    } catch (failure) {
      setFormError(
        failureMessage(failure, "Unable to replace role permissions."),
      );
    } finally {
      setPending(false);
    }
  }

  async function confirmArchive() {
    if (!archiveTarget) return;
    setPending(true);
    setArchiveError(null);
    setFormError(null);
    try {
      await archiveRole(archiveTarget.id);
      setSuccess(`${archiveTarget.name} archived.`);
      setArchiveTarget(null);
      await reloadRoles();
    } catch (failure) {
      setArchiveError(failureMessage(failure, "Unable to archive the role."));
    } finally {
      setPending(false);
    }
  }

  async function restoreRole(role: Role) {
    setPending(true);
    setFormError(null);
    setSuccess(null);
    try {
      await updateRole(role.id, { is_active: true });
      await reloadRoles();
      setSuccess(`${role.name} restored.`);
    } catch (failure) {
      setFormError(failureMessage(failure, "Unable to restore the role."));
    } finally {
      setPending(false);
    }
  }

  const loadedRoles = roles ?? [];
  const protectedCount = loadedRoles.filter((role) => role.is_protected).length;
  const customCount = loadedRoles.length - protectedCount;
  const inactiveCount = loadedRoles.filter((role) => !role.is_active).length;
  const assignedTotal = loadedRoles.reduce(
    (sum, role) => sum + role.assignment_count,
    0,
  );
  const query = roleQuery.trim().toLowerCase();
  const visibleRoles = loadedRoles
    .slice()
    .sort(compareRoles)
    .filter((role) => {
      if (roleKind === "protected" && !role.is_protected) return false;
      if (roleKind === "custom" && role.is_protected) return false;
      if (roleKind === "inactive" && role.is_active) return false;
      if (!query) return true;
      return (
        role.name.toLowerCase().includes(query) ||
        role.key.toLowerCase().includes(query) ||
        role.description.toLowerCase().includes(query) ||
        role.permission_codes.some((code) => code.toLowerCase().includes(query))
      );
    });
  const catalogQueryNormalized = catalogQuery.trim().toLowerCase();
  const visibleCatalog = (catalog ?? []).filter((permission) => {
    if (!catalogQueryNormalized) return true;
    const availability = catalogAvailability(permission.code) ?? "";
    return `${permission.code} ${permission.module} ${permission.description} ${moduleLabel(permission.module)} ${availability}`
      .toLowerCase()
      .includes(catalogQueryNormalized);
  });
  const dialogOpen =
    creatingRole || editingRoleId !== null || permissionsRoleId !== null;
  let guidance: string | null = null;
  if (canManage && isOwner) {
    guidance =
      "Protected role names and archive stay fixed. An owner can replace permissions on protected roles other than Owner, including the manager policy. Owner identity and permissions stay unchanged.";
  } else if (canManage) {
    guidance =
      "Protected role identity, archive, and permissions stay with an active owner. Custom role changes must stay inside your live permissions and the manager delegation policy.";
  }
  const filters: { id: RoleKind; label: string; count: number }[] = [
    { id: "all", label: "All", count: loadedRoles.length },
    { id: "protected", label: "Protected", count: protectedCount },
    { id: "custom", label: "Custom", count: customCount },
    { id: "inactive", label: "Inactive", count: inactiveCount },
  ];
  const roleGroups = [
    {
      title: "Protected roles",
      roles: visibleRoles.filter((role) => role.is_protected),
    },
    {
      title: "Custom roles",
      roles: visibleRoles.filter((role) => !role.is_protected),
    },
  ].filter((group) => group.roles.length > 0);

  return (
    <div className="grid gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div
          aria-label="Role sections"
          className="inline-flex w-fit rounded-xl bg-slate-200/70 p-1"
          role="tablist"
        >
          {(
            [
              ["roles", "Roles"],
              ["catalog", "Permission catalog"],
            ] as const
          ).map(([id, label]) => (
            <button
              aria-controls={`${id}-panel`}
              aria-selected={section === id}
              className={tabClass(section === id)}
              id={`${id}-tab`}
              key={id}
              onClick={() => setSection(id)}
              role="tab"
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        {canManage && section === "roles" ? (
          <button
            className="button button-create w-full sm:w-fit"
            onClick={openCreate}
            type="button"
          >
            Create role
          </button>
        ) : null}
      </div>

      {section === "roles" ? (
        <div
          aria-labelledby="roles-tab"
          className="grid gap-4"
          id="roles-panel"
          role="tabpanel"
        >
          {guidance ? (
            <p className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-600 shadow-sm">
              {guidance}
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
          {rolesError ? (
            <p className="error" role="alert">
              {rolesError}
            </p>
          ) : null}
          {formError && !dialogOpen ? (
            <p className="error" role="alert">
              {formError}
            </p>
          ) : null}
          {roles === null ? (
            <div className={`${panelClass} grid gap-3 p-5`}>
              <p className="text-sm font-medium text-slate-500" role="status">
                Loading roles.
              </p>
              {[0, 1, 2].map((item) => (
                <div
                  className="h-16 animate-pulse rounded-xl bg-slate-100"
                  key={item}
                />
              ))}
            </div>
          ) : null}
          {roles !== null && loadedRoles.length === 0 && !rolesError ? (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center shadow-sm">
              <p className="font-semibold text-slate-800" role="status">
                No roles are defined for this gym.
              </p>
              <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
                {canManage
                  ? "Create a custom role, then choose the permissions it should grant."
                  : "Roles appear here after an owner adds them."}
              </p>
            </div>
          ) : null}

          {creatingRole ? (
            <FormDialog
              description="A key is assigned from the role name."
              noValidate
              onClose={() => {
                if (!pending) setCreatingRole(false);
              }}
              onSubmit={(event) => void submitCreate(event)}
              pending={pending}
              title="Create role"
            >
              <div className="grid gap-4">
                {formError ? (
                  <p className="error" role="alert">
                    {formError}
                  </p>
                ) : null}
                <Field
                  error={errorScope === "create" ? fieldErrors.name : undefined}
                  label="Name"
                  maxLength={100}
                  name="name"
                  placeholder="Front desk"
                  required
                />
                <Field
                  error={
                    errorScope === "create"
                      ? fieldErrors.description
                      : undefined
                  }
                  label="Description"
                  maxLength={255}
                  name="description"
                  placeholder="What this role is for"
                />
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
                  <label className="flex items-center gap-3 text-sm font-semibold text-slate-800">
                    <input
                      className="size-4 accent-emerald-700"
                      defaultChecked
                      name="is_active"
                      type="checkbox"
                    />
                    Active
                  </label>
                  <p className="mt-1 pl-7 text-xs leading-5 text-slate-500">
                    People can be assigned to an active role.
                  </p>
                </div>
                <div>
                  <button
                    className="button button-create"
                    disabled={pending}
                    type="submit"
                  >
                    {pending ? "Saving…" : "Create role"}
                  </button>
                </div>
              </div>
            </FormDialog>
          ) : null}

          {roles !== null && loadedRoles.length > 0 ? (
            <section aria-label="Role list" className={panelClass}>
              <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
                <h2 className="text-lg font-bold text-slate-900">Role list</h2>
                <p className="mt-0.5 text-sm text-slate-500">
                  {visibleRoles.length === loadedRoles.length
                    ? `${loadedRoles.length} ${loadedRoles.length === 1 ? "role" : "roles"} · ${protectedCount} protected · ${customCount} custom · ${assignedTotal} assigned`
                    : `${visibleRoles.length} of ${loadedRoles.length} roles`}
                </p>
              </div>
              <div className="border-b border-slate-100 bg-slate-50/80 p-4 sm:p-5">
                <label className="field">
                  <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                    Search roles
                  </span>
                  <input
                    className="input"
                    onChange={(event) => setRoleQuery(event.target.value)}
                    placeholder="Name, key, or permission"
                    type="search"
                    value={roleQuery}
                  />
                </label>
                <div className="mt-3 flex flex-wrap gap-2">
                  {filters
                    .filter(
                      (filter) =>
                        filter.id !== "inactive" ||
                        filter.count > 0 ||
                        roleKind === "inactive",
                    )
                    .map((filter) => (
                      <button
                        aria-pressed={roleKind === filter.id}
                        className={filterClass(roleKind === filter.id)}
                        key={filter.id}
                        onClick={() => setRoleKind(filter.id)}
                        type="button"
                      >
                        {filter.label}
                        <span
                          aria-hidden="true"
                          className="ml-1.5 text-xs opacity-70"
                        >
                          {filter.count}
                        </span>
                      </button>
                    ))}
                  {roleQuery ? (
                    <button
                      className="button button-cancel button-sm"
                      onClick={() => setRoleQuery("")}
                      type="button"
                    >
                      Clear
                    </button>
                  ) : null}
                </div>
              </div>
              {visibleRoles.length === 0 ? (
                <div className="px-6 py-14 text-center">
                  <p className="font-semibold text-slate-800">
                    No roles match this search.
                  </p>
                  <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-slate-500">
                    Try another name, key, or permission.
                  </p>
                </div>
              ) : (
                <div className="grid gap-6 p-4 sm:p-5">
                  {roleGroups.map((group) => (
                    <div key={group.title}>
                      <p className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                        {group.title}
                      </p>
                      <div className="mt-3 grid gap-3">
                        {group.roles.map((role) => {
                          const customMutable =
                            canManage &&
                            canMutateCustomRole(role, {
                              isOwner,
                              livePermissions: permissions,
                              roles: loadedRoles,
                            });
                          const options =
                            catalog === null || catalogError
                              ? null
                              : permissionOptions({
                                  role,
                                  catalog,
                                  roles: loadedRoles,
                                  isOwner,
                                  canManage,
                                  livePermissions: permissions,
                                });
                          return (
                            <RoleCard
                              customMutable={customMutable}
                              editing={editingRoleId === role.id}
                              errorScope={errorScope}
                              fieldErrors={fieldErrors}
                              formError={
                                editingRoleId === role.id ||
                                permissionsRoleId === role.id
                                  ? formError
                                  : null
                              }
                              key={role.id}
                              onArchive={() => {
                                setArchiveError(null);
                                setArchiveTarget(role);
                                setCreatingRole(false);
                                setEditingRoleId(null);
                                setPermissionsRoleId(null);
                              }}
                              onCloseEdit={() => {
                                if (!pending) setEditingRoleId(null);
                              }}
                              onClosePermissions={() => {
                                if (!pending) setPermissionsRoleId(null);
                              }}
                              onEdit={() => openEdit(role.id)}
                              onOpenPermissions={() => openPermissions(role.id)}
                              onRestore={() => void restoreRole(role)}
                              onSubmitIdentity={(event) =>
                                void submitIdentity(event, role)
                              }
                              onSubmitPermissions={(event, current) =>
                                void submitPermissions(
                                  event,
                                  current,
                                  options ?? [],
                                )
                              }
                              options={options}
                              outsideBoundary={
                                canManage &&
                                isCustomRole(role) &&
                                !customMutable
                              }
                              pending={pending}
                              permissionsOpen={permissionsRoleId === role.id}
                              role={role}
                            />
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          ) : null}
        </div>
      ) : null}

      {section === "catalog" ? (
        <section
          aria-labelledby="catalog-tab"
          className={panelClass}
          id="catalog-panel"
          role="tabpanel"
        >
          <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
            <h2 className="text-lg font-bold text-slate-900">
              Permission catalog
            </h2>
            <p className="mt-0.5 max-w-2xl text-sm leading-6 text-slate-500">
              {catalog && catalog.length > 0
                ? `${catalog.length} permissions, grouped by area. A role only receives the ones saved onto it.`
                : "Every permission this gym can grant."}
            </p>
          </div>
          {catalog === null ? (
            <div className="grid gap-3 p-5">
              <p className="text-sm font-medium text-slate-500" role="status">
                Loading permissions.
              </p>
              {[0, 1, 2].map((item) => (
                <div
                  className="h-12 animate-pulse rounded-xl bg-slate-100"
                  key={item}
                />
              ))}
            </div>
          ) : null}
          {catalogError ? (
            <p className="error m-4" role="alert">
              {catalogError}
            </p>
          ) : null}
          {catalog !== null && catalog.length === 0 && !catalogError ? (
            <div className="px-6 py-16 text-center">
              <p className="font-semibold text-slate-800" role="status">
                No permissions are in the catalog.
              </p>
            </div>
          ) : null}
          {catalog && catalog.length > 0 ? (
            <>
              <div className="border-b border-slate-100 bg-slate-50/80 p-4 sm:p-5">
                <label className="field">
                  <span className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                    Search permissions
                  </span>
                  <input
                    className="input"
                    onChange={(event) => setCatalogQuery(event.target.value)}
                    placeholder="Code, area, or note"
                    type="search"
                    value={catalogQuery}
                  />
                </label>
              </div>
              {visibleCatalog.length === 0 ? (
                <div className="px-6 py-14 text-center">
                  <p className="font-semibold text-slate-800">
                    No permissions match this search.
                  </p>
                </div>
              ) : (
                groupPermissions(visibleCatalog).map(([module, items]) => (
                  <div key={module}>
                    <div className="flex items-center justify-between bg-slate-50 px-4 py-2 sm:px-5">
                      <h3 className="text-xs font-bold tracking-wide text-slate-500 uppercase">
                        {moduleLabel(module)}
                      </h3>
                      <span className="text-xs font-medium text-slate-400">
                        {items.length}
                      </span>
                    </div>
                    <ul className="divide-y divide-slate-100">
                      {items.map((permission) => {
                        const availability = catalogAvailability(
                          permission.code,
                        );
                        const detail = permissionDetail(permission);
                        return (
                          <li className="px-4 py-3 sm:px-5" key={permission.id}>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="font-mono text-sm font-semibold break-all text-slate-900">
                                {permission.code}
                              </p>
                              {permission.is_active ? null : (
                                <Pill tone="amber">Inactive</Pill>
                              )}
                            </div>
                            {detail ? (
                              <p className="mt-1 text-sm leading-5 text-slate-500">
                                {detail}
                              </p>
                            ) : null}
                            {availability ? (
                              <p className="mt-1 text-sm leading-5 text-amber-800">
                                {availability}
                              </p>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))
              )}
            </>
          ) : null}
        </section>
      ) : null}

      {archiveTarget ? (
        <ArchiveDialog
          error={archiveError}
          onCancel={() => {
            if (!pending) setArchiveTarget(null);
          }}
          onConfirm={() => void confirmArchive()}
          pending={pending}
          roleName={archiveTarget.name}
        />
      ) : null}
    </div>
  );
}

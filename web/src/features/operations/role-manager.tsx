"use client";

import {
  useEffect,
  useId,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  archiveRole,
  createRole,
  listAllPermissions,
  listAllRoles,
  replaceRolePermissions,
  updateRole,
} from "@/features/operations/client";
import {
  PROTECTED_ROLE_KEYS,
  canMutateCustomRole,
  catalogAvailability,
  isCustomRole,
  permissionOptions,
  type PermissionOption,
} from "@/features/operations/role-policy";
import type { Permission, Role, RoleCreate, RoleUpdate } from "@/types/api";

const ROLE_KEY = /^[A-Z][A-Z0-9_]{0,31}$/;

type FieldErrors = Record<string, string>;
type IdentityValue = {
  key: string;
  name: string;
  description: string;
  is_active?: boolean;
};
type IdentityResult =
  | { ok: true; value: IdentityValue }
  | { ok: false; errors: FieldErrors; message: string };

function failureMessage(failure: unknown, fallback: string): string {
  return failure instanceof Error && failure.message
    ? failure.message
    : fallback;
}

function readIdentity(form: FormData, includeActive: boolean): IdentityResult {
  const key = String(form.get("key") ?? "").trim();
  const name = String(form.get("name") ?? "").trim();
  const description = String(form.get("description") ?? "").trim();
  const errors: FieldErrors = {};
  if (!ROLE_KEY.test(key) || PROTECTED_ROLE_KEYS.has(key)) {
    errors.key = PROTECTED_ROLE_KEYS.has(key)
      ? "This role key is reserved."
      : "Enter an uppercase role key using letters, numbers, and underscores.";
  }
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
  const value: IdentityValue = { key, name, description };
  if (includeActive) value.is_active = form.get("is_active") === "on";
  return { ok: true, value };
}

function Field({
  label,
  name,
  error,
  defaultValue = "",
  maxLength,
  required = false,
}: {
  label: string;
  name: string;
  error?: string | undefined;
  defaultValue?: string;
  maxLength?: number;
  required?: boolean;
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
        required={required}
        defaultValue={defaultValue}
        maxLength={maxLength}
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
  const title = `Archive ${roleName}`;
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
          Archive {roleName}? The custom role becomes inactive. Protected roles
          are not archived here.
        </p>
        {error ? (
          <p className="error mt-4" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            className="button button-archive"
            type="button"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? "Archiving…" : "Confirm archive"}
          </button>
          <button
            className="button button-cancel"
            type="button"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function PermissionChoices({
  option,
  checked,
}: {
  option: PermissionOption;
  checked: boolean;
}) {
  const reasonId = useId();
  if (option.mode === "unavailable") {
    return (
      <li>
        <span className="font-medium">{option.code}</span>
        <span className="muted ml-2">{option.reason}</span>
      </li>
    );
  }
  return (
    <li>
      <label className="flex items-center gap-2">
        <input
          name="permission_codes"
          type="checkbox"
          value={option.code}
          defaultChecked={checked}
          disabled={option.mode === "required"}
          aria-describedby={option.reason ? reasonId : undefined}
        />
        <span>{option.code}</span>
      </label>
      {option.description ? (
        <p className="muted text-sm">{option.description}</p>
      ) : null}
      {option.reason ? (
        <p id={reasonId} className="muted text-sm">
          {option.reason}
        </p>
      ) : null}
    </li>
  );
}

function PermissionEditor({
  role,
  options,
  pending,
  onSubmit,
}: {
  role: Role;
  options: PermissionOption[];
  pending: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>, role: Role) => void;
}) {
  const kept = new Set(
    options
      .filter((option) => option.mode !== "unavailable")
      .map((option) => option.code),
  );
  const dropped = role.permission_codes.filter((code) => !kept.has(code));
  const editable = new Set(
    options
      .filter((option) => option.mode === "editable")
      .map((option) => option.code),
  );
  return (
    <form
      className="mt-4"
      noValidate
      onSubmit={(event) => onSubmit(event, role)}
    >
      <fieldset className="grid gap-2">
        <legend className="font-semibold">Permissions for {role.name}</legend>
        {dropped.length > 0 ? (
          <p>
            Saving replaces the full permission set. Permissions outside the
            delegation policy are removed.
          </p>
        ) : null}
        <ul className="grid gap-2">
          {options.map((option) => (
            <PermissionChoices
              key={option.code}
              option={option}
              checked={
                option.mode === "required" ||
                role.permission_codes.includes(option.code)
              }
            />
          ))}
        </ul>
        {editable.size === 0 &&
        !options.some((option) => option.mode === "required") ? (
          <p className="muted">
            No permissions in the current delegation policy can be selected.
          </p>
        ) : null}
        <button
          className="button button-edit mt-2"
          disabled={pending}
          type="submit"
        >
          {pending ? "Saving…" : `Save permissions for ${role.name}`}
        </button>
      </fieldset>
    </form>
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
  const [catalog, setCatalog] = useState<Permission[] | null>(null);
  const [rolesError, setRolesError] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [errorScope, setErrorScope] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<Role | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);

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
      key: parsed.value.key,
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
      key: parsed.value.key,
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
  let guidance: ReactNode = null;
  if (canManage && isOwner) {
    guidance = (
      <p className="muted">
        Protected role names and archive stay fixed. An owner can replace
        permissions on protected roles other than Owner, including the manager
        policy. Owner identity and permissions stay unchanged.
      </p>
    );
  } else if (canManage) {
    guidance = (
      <p className="muted">
        Protected role identity, archive, and permissions stay with an active
        owner. Custom role changes must stay inside your live permissions and
        the manager delegation policy.
      </p>
    );
  }

  return (
    <div className="grid gap-6">
      {guidance}
      {roles === null ? <p role="status">Loading roles.</p> : null}
      {rolesError ? (
        <p className="error" role="alert">
          {rolesError}
        </p>
      ) : null}
      {formError ? (
        <p className="error" role="alert">
          {formError}
        </p>
      ) : null}
      {success ? <p role="status">{success}</p> : null}
      {roles !== null && loadedRoles.length === 0 && !rolesError ? (
        <p role="status">No roles are defined for this gym.</p>
      ) : null}

      {canManage ? (
        <section
          className="rounded-lg border border-slate-200 bg-white p-5"
          aria-label="Create role"
        >
          <h2 className="text-xl font-bold">Create role</h2>
          <form
            className="mt-4 grid gap-3 md:grid-cols-2"
            noValidate
            onSubmit={(event) => void submitCreate(event)}
          >
            <Field
              label="Key"
              name="key"
              required
              maxLength={32}
              error={errorScope === "create" ? fieldErrors.key : undefined}
            />
            <Field
              label="Name"
              name="name"
              required
              maxLength={100}
              error={errorScope === "create" ? fieldErrors.name : undefined}
            />
            <Field
              label="Description"
              name="description"
              maxLength={255}
              error={
                errorScope === "create" ? fieldErrors.description : undefined
              }
            />
            <label className="flex items-center gap-2">
              <input name="is_active" type="checkbox" defaultChecked />
              <span>Active</span>
            </label>
            <button
              className="button button-create md:col-span-2"
              disabled={pending}
              type="submit"
            >
              {pending ? "Saving…" : "Create role"}
            </button>
          </form>
        </section>
      ) : null}

      <div className="grid gap-4">
        {loadedRoles.map((role) => {
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
          const outsideBoundary =
            canManage && isCustomRole(role) && !customMutable;
          return (
            <article
              key={role.id}
              className="rounded-lg border border-slate-200 bg-white p-5"
              aria-labelledby={`role-${role.id}`}
            >
              <h2 id={`role-${role.id}`} className="text-xl font-bold">
                {role.name}
              </h2>
              <p className="muted">{role.key}</p>
              <dl className="mt-3 grid gap-2 text-sm">
                <div className="grid gap-1 sm:grid-cols-[12rem_1fr]">
                  <dt className="muted">Status</dt>
                  <dd>{role.is_protected ? "Protected" : "Custom"}</dd>
                </div>
                <div className="grid gap-1 sm:grid-cols-[12rem_1fr]">
                  <dt className="muted">Activity</dt>
                  <dd>{role.is_active ? "Active" : "Inactive"}</dd>
                </div>
                <div className="grid gap-1 sm:grid-cols-[12rem_1fr]">
                  <dt className="muted">Assignment count</dt>
                  <dd>{role.assignment_count}</dd>
                </div>
              </dl>
              {role.description ? (
                <p className="mt-3">{role.description}</p>
              ) : null}
              <h3 className="mt-4 font-semibold">Permissions</h3>
              {role.permission_codes.length === 0 ? (
                <p className="muted">No permissions assigned.</p>
              ) : (
                <ul aria-label={`Permissions on ${role.name}`}>
                  {role.permission_codes.map((code) => (
                    <li key={code}>{code}</li>
                  ))}
                </ul>
              )}
              {outsideBoundary ? (
                <p className="muted mt-3">
                  This role is outside your delegation boundary.
                </p>
              ) : null}
              {customMutable ? (
                <form
                  className="mt-4 grid gap-3 md:grid-cols-2"
                  key={`${role.id}:${role.updated_at}:identity`}
                  noValidate
                  onSubmit={(event) => void submitIdentity(event, role)}
                >
                  <Field
                    label="Key"
                    name="key"
                    required
                    maxLength={32}
                    defaultValue={role.key}
                    error={errorScope === role.id ? fieldErrors.key : undefined}
                  />
                  <Field
                    label="Name"
                    name="name"
                    required
                    maxLength={100}
                    defaultValue={role.name}
                    error={
                      errorScope === role.id ? fieldErrors.name : undefined
                    }
                  />
                  <Field
                    label="Description"
                    name="description"
                    maxLength={255}
                    defaultValue={role.description}
                    error={
                      errorScope === role.id
                        ? fieldErrors.description
                        : undefined
                    }
                  />
                  <div className="flex flex-wrap gap-2 md:col-span-2">
                    <button
                      className="button button-edit"
                      disabled={pending}
                      type="submit"
                    >
                      {pending ? "Saving…" : `Save ${role.name}`}
                    </button>
                    {role.is_active ? (
                      <button
                        className="button button-archive"
                        type="button"
                        disabled={pending}
                        onClick={() => {
                          setArchiveError(null);
                          setArchiveTarget(role);
                        }}
                      >
                        Archive {role.name}
                      </button>
                    ) : (
                      <button
                        className="button button-success"
                        type="button"
                        disabled={pending}
                        onClick={() => void restoreRole(role)}
                      >
                        Restore {role.name}
                      </button>
                    )}
                  </div>
                </form>
              ) : null}
              {options ? (
                <PermissionEditor
                  key={`${role.id}:${role.updated_at}:${role.permission_codes.join(",")}`}
                  role={role}
                  options={options}
                  pending={pending}
                  onSubmit={(event, current) =>
                    void submitPermissions(event, current, options)
                  }
                />
              ) : null}
            </article>
          );
        })}
      </div>

      <section
        className="rounded-lg border border-slate-200 bg-white p-5"
        aria-label="Permission catalog"
      >
        <h2 className="text-xl font-bold">Permission catalog</h2>
        {catalog === null ? (
          <p className="mt-4" role="status">
            Loading permissions.
          </p>
        ) : null}
        {catalogError ? (
          <p className="error mt-4" role="alert">
            {catalogError}
          </p>
        ) : null}
        {catalog !== null && catalog.length === 0 && !catalogError ? (
          <p className="mt-4" role="status">
            No permissions are in the catalog.
          </p>
        ) : null}
        {catalog && catalog.length > 0 ? (
          <ul className="mt-4 grid gap-3">
            {catalog.map((permission) => {
              const availability = catalogAvailability(permission.code);
              return (
                <li key={permission.id}>
                  <span className="font-medium">{permission.code}</span>
                  <span className="muted ml-2">{permission.module}</span>
                  {permission.description ? (
                    <span className="ml-2">{permission.description}</span>
                  ) : null}
                  {availability ? (
                    <span className="mt-1 block">{availability}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      {archiveTarget ? (
        <ArchiveDialog
          roleName={archiveTarget.name}
          pending={pending}
          error={archiveError}
          onCancel={() => {
            if (!pending) setArchiveTarget(null);
          }}
          onConfirm={() => void confirmArchive()}
        />
      ) : null}
    </div>
  );
}

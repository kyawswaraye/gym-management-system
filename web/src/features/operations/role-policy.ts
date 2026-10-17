import type { Permission, Role } from "@/types/api";

export const HARD_RESERVED_APPROVALS = [
  "payments.approve",
  "expenses.approve",
  "retail.approve",
  "pt.sales.approve",
  "salaries.approve",
] as const;

export const OWNER_ONLY_PERMISSION = "settings.manage";

export const PROTECTED_ROLE_KEYS = new Set([
  "OWNER",
  "MANAGER",
  "RECEPTIONIST",
  "ACCOUNTANT",
  "TRAINER",
]);

export const HARD_RESERVED_LABEL =
  "Hard-reserved approval. Unavailable outside the manager role.";

export const SETTINGS_MANAGE_LABEL =
  "Owner-only setting. Unavailable on roles.";

export const MANAGER_REQUIRED_LABEL =
  "Hard-reserved approval. Required on the manager role.";

const HARD_RESERVED = new Set<string>(HARD_RESERVED_APPROVALS);

export type PermissionMode = "editable" | "required" | "unavailable";

export type PermissionOption = {
  code: string;
  description: string;
  mode: PermissionMode;
  reason: string;
};

export function catalogAvailability(code: string): string | null {
  if (HARD_RESERVED.has(code)) return HARD_RESERVED_LABEL;
  if (code === OWNER_ONLY_PERMISSION) return SETTINGS_MANAGE_LABEL;
  return null;
}

export function delegablePermissionCodes(roles: readonly Role[]): Set<string> {
  const manager = roles.find(
    (role) => role.key === "MANAGER" && role.is_protected && role.is_active,
  );
  if (!manager) return new Set();
  return new Set(
    manager.permission_codes.filter(
      (code) =>
        !HARD_RESERVED.has(code) &&
        code !== OWNER_ONLY_PERMISSION &&
        !code.startsWith("platform."),
    ),
  );
}

export function isCustomRole(role: Role): boolean {
  return !role.is_protected && role.key !== "OWNER";
}

function withinLiveBoundary(
  codes: readonly string[],
  livePermissions: readonly string[],
  delegable: ReadonlySet<string>,
): boolean {
  const live = new Set(livePermissions);
  return codes.every(
    (code) =>
      delegable.has(code) &&
      live.has(code) &&
      !HARD_RESERVED.has(code) &&
      code !== OWNER_ONLY_PERMISSION &&
      !code.startsWith("platform."),
  );
}

export function isProtectedManagerRole(
  role: Pick<Role, "key" | "is_protected">,
): boolean {
  return role.key === "MANAGER" && role.is_protected;
}

/** Safe client mirror of the server role-grant check. The server revalidates. */
export function roleIsAssignable(
  role: Role,
  input: {
    isOwner: boolean;
    livePermissions: readonly string[];
    roles: readonly Role[];
  },
): boolean {
  if (role.key === "OWNER" || !role.is_active) return false;
  const codes = role.permission_codes;
  if (codes.some((code) => code.startsWith("platform."))) return false;
  if (isProtectedManagerRole(role)) {
    return input.isOwner && !codes.includes(OWNER_ONLY_PERMISSION);
  }
  if (
    codes.some(
      (code) => HARD_RESERVED.has(code) || code === OWNER_ONLY_PERMISSION,
    )
  ) {
    return false;
  }
  const delegable = delegablePermissionCodes(input.roles);
  if (input.isOwner) return codes.every((code) => delegable.has(code));
  const live = new Set(input.livePermissions);
  return codes.every((code) => delegable.has(code) && live.has(code));
}

export function assignableRoles(
  roles: readonly Role[],
  input: { isOwner: boolean; livePermissions: readonly string[] },
): Role[] {
  return roles.filter((role) => roleIsAssignable(role, { ...input, roles }));
}

/** True when saving would drop a role this caller cannot grant. */
export function assignmentRequiresOwner(
  assigned: readonly Role[],
  catalog: readonly Role[],
  input: { isOwner: boolean; livePermissions: readonly string[] },
): boolean {
  const allowed = new Set(
    assignableRoles(catalog, input).map((role) => role.id),
  );
  return assigned.some((role) => role.key === "OWNER" || !allowed.has(role.id));
}

export function canMutateCustomRole(
  role: Role,
  input: {
    isOwner: boolean;
    livePermissions: readonly string[];
    roles: readonly Role[];
  },
): boolean {
  if (!isCustomRole(role)) return false;
  if (input.isOwner) return true;
  return withinLiveBoundary(
    role.permission_codes,
    input.livePermissions,
    delegablePermissionCodes(input.roles),
  );
}

export function permissionOptions(input: {
  role: Role;
  catalog: readonly Permission[];
  roles: readonly Role[];
  isOwner: boolean;
  canManage: boolean;
  livePermissions: readonly string[];
}): PermissionOption[] | null {
  if (!input.canManage || !input.role.is_active) return null;
  if (input.role.key === "OWNER") return null;
  if (input.role.is_protected && !input.isOwner) return null;
  if (!input.role.is_protected && !canMutateCustomRole(input.role, input)) {
    return null;
  }

  const delegable = delegablePermissionCodes(input.roles);
  const live = new Set(input.livePermissions);
  const managerPolicy =
    input.role.key === "MANAGER" && input.role.is_protected && input.isOwner;
  const catalogByCode = new Map(
    input.catalog
      .filter(
        (permission) =>
          permission.is_active && !permission.code.startsWith("platform."),
      )
      .map((permission) => [permission.code, permission]),
  );
  const options: PermissionOption[] = [];

  for (const code of HARD_RESERVED_APPROVALS) {
    options.push({
      code,
      description: catalogByCode.get(code)?.description ?? "",
      mode: managerPolicy ? "required" : "unavailable",
      reason: managerPolicy ? MANAGER_REQUIRED_LABEL : HARD_RESERVED_LABEL,
    });
  }
  options.push({
    code: OWNER_ONLY_PERMISSION,
    description: catalogByCode.get(OWNER_ONLY_PERMISSION)?.description ?? "",
    mode: "unavailable",
    reason: SETTINGS_MANAGE_LABEL,
  });

  const editable = [...catalogByCode.keys()]
    .filter(
      (code) => !HARD_RESERVED.has(code) && code !== OWNER_ONLY_PERMISSION,
    )
    .filter((code) => managerPolicy || delegable.has(code))
    .filter((code) => input.isOwner || live.has(code))
    .sort();
  for (const code of editable) {
    const permission = catalogByCode.get(code);
    if (!permission) continue;
    options.push({
      code,
      description: permission.description,
      mode: "editable",
      reason: "",
    });
  }
  return options;
}

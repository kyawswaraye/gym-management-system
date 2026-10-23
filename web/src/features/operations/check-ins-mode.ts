export type CheckInWorkspaceMode = "desk" | "qr";

export function checkInViewValue(
  value: string | string[] | undefined,
): string | null {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.find((item) => item.length > 0) ?? null;
  }
  return null;
}

export function checkInWorkspaceMode(input: {
  canDesk: boolean;
  canQr: boolean;
  view?: string | null;
}): CheckInWorkspaceMode | null {
  if (input.canQr && !input.canDesk) return "qr";
  if (input.canDesk && !input.canQr) return "desk";
  if (input.canDesk && input.canQr) {
    return input.view === "qr" ? "qr" : "desk";
  }
  return null;
}

export function checkInModeHref(mode: CheckInWorkspaceMode): string {
  return mode === "qr" ? "/check-ins?view=qr" : "/check-ins";
}

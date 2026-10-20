const SCAN_MESSAGES: Record<string, string> = {
  CHECK_IN_CODE_INVALID:
    "That check-in code is not valid. Ask the front desk to show a new code.",
  CHECK_IN_DUPLICATE: "You were already checked in during this visit window.",
  CHECK_IN_OPEN:
    "You are already checked in. Check out at the desk before scanning again.",
  CHECK_IN_INELIGIBLE: "Your membership is not eligible for check-in.",
};

export function scanErrorMessage(code: string, fallback: string): string {
  return SCAN_MESSAGES[code] ?? fallback;
}

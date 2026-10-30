const SCAN_MESSAGES: Record<string, string> = {
  CHECK_IN_CODE_INVALID:
    "That code expired or changed. Wait for the new code on the gym screen, then scan again.",
  CHECK_IN_DUPLICATE: "You were already checked in during this visit window.",
  CHECK_IN_OPEN:
    "You are already checked in. Check out in the app before scanning again.",
  CHECK_IN_INELIGIBLE: "Your membership is not eligible for check-in.",
};

export function scanErrorMessage(code: string, fallback: string): string {
  return SCAN_MESSAGES[code] ?? fallback;
}

import { OperationError } from "@/features/operations/client";

export const PAID_PT_GRANT =
  "This PT pack cannot be given as a paid grant. Choose Free pack, or sell it on PT sales and approve that sale.";

export const TRAINER_LINK_NEEDED =
  "This member is not linked to this trainer on the sale date. Link them here, then save again.";

export function phase5Error(caught: unknown, fallback: string): string {
  if (caught instanceof OperationError) {
    if (caught.code === "SALE_GRANT_REQUIRED") return PAID_PT_GRANT;
    if (caught.code === "ASSIGNMENT_NOT_EFFECTIVE") return TRAINER_LINK_NEEDED;
    if (caught.code === "PACKAGE_CONSUMED") {
      return "PACKAGE_CONSUMED: This package has already been used, so the sale cannot be voided.";
    }
    if (caught.code === "CLASS_CUTOFF") {
      return "CLASS_CUTOFF: This booking is inside the cancellation cutoff.";
    }
    if (caught.code === "CLASS_FULL") {
      return "CLASS_FULL: This class is at capacity.";
    }
    if (caught.code === "BOOKING_STATE_CONFLICT") {
      return "Too early. Mark attended after the class starts. Mark no-show after it ends.";
    }
    if (caught.code) return `${caught.code}: ${caught.message || fallback}`;
    return caught.message || fallback;
  }
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

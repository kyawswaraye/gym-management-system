import { OperationError } from "@/features/operations/client";

export function phase5Error(caught: unknown, fallback: string): string {
  if (caught instanceof OperationError) {
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

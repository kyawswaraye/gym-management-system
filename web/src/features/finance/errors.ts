import { OperationError } from "@/features/operations/client";

export function financeError(caught: unknown, fallback: string): string {
  if (caught instanceof OperationError) {
    if (caught.code === "RETAIL_CORRECTION_UNAVAILABLE") {
      return "This product payment cannot be corrected here. Void the retail sale instead. The retail full void keeps the payment, receipt, and stock return together.";
    }
    if (caught.code === "PROTECTED_EXPENSE_CATEGORY") {
      return "Salary and commission expenses are protected. Void the salary period instead of changing this expense.";
    }
    return caught.message || fallback;
  }
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

export function fieldError(caught: unknown, field: string): string | undefined {
  if (!(caught instanceof OperationError)) return undefined;
  return caught.fields[field];
}

export function fieldMap(caught: unknown): Record<string, string> {
  return caught instanceof OperationError ? caught.fields : {};
}

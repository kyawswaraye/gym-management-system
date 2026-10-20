import type { FieldErrors, Resolver } from "react-hook-form";
import type { ZodType } from "zod";

export function zodFormResolver<T extends Record<string, unknown>>(
  schema: ZodType<T>,
): Resolver<T> {
  return async (values) => {
    const parsed = schema.safeParse(values);
    if (parsed.success) {
      return { values: parsed.data, errors: {} };
    }
    const errors: Record<string, { type: string; message: string }> = {};
    for (const issue of parsed.error.issues) {
      const path = issue.path.map(String).join(".");
      if (path && !errors[path]) {
        errors[path] = { type: issue.code, message: issue.message };
      }
    }
    return { values: {} as never, errors: errors as FieldErrors<T> };
  };
}

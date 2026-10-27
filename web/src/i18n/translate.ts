import type { Locale } from "@/i18n/locale";
import { messages } from "@/i18n/messages";

export type Vars = Record<string, string | number>;

function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function translate(locale: Locale, text: string, vars?: Vars): string {
  const key = normalize(text);
  const table = locale === "mm" ? messages.mm : null;
  const template = table?.[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (token, name: string) => {
    const value = vars[name];
    return value === undefined ? token : String(value);
  });
}

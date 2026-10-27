import { getActiveLocale, type Locale } from "./locale";
import { messages } from "./messages";

export type Vars = Record<string, string | number>;

export function translate(locale: Locale, text: string, vars?: Vars): string {
  if (typeof text !== "string") {
    return "";
  }
  const key = text.replace(/\s+/g, " ").trim();
  const template = locale === "mm" ? (messages[key] ?? key) : key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (token, name: string) => {
    const value = vars[name];
    return value === undefined ? token : String(value);
  });
}

export function tx(text: string, vars?: Vars): string {
  return translate(getActiveLocale(), text, vars);
}

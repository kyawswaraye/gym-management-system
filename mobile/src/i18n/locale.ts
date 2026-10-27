export type Locale = "en" | "mm";

let activeLocale: Locale = "en";

export function parseLocale(value: string | null | undefined): Locale {
  return value === "mm" ? "mm" : "en";
}

export function getActiveLocale(): Locale {
  return activeLocale;
}

export function setActiveLocale(locale: Locale): void {
  activeLocale = locale;
}

export function acceptLanguage(locale: Locale = activeLocale): "en" | "my" {
  return locale === "mm" ? "my" : "en";
}

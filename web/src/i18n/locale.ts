export const LOCALE_COOKIE = "gp_locale";

export type Locale = "en" | "mm";

export function parseLocale(value: string | undefined): Locale {
  return value === "mm" ? "mm" : "en";
}

export function htmlLang(locale: Locale): "en" | "my" {
  return locale === "mm" ? "my" : "en";
}

export function acceptLanguage(locale: Locale): "en" | "my" {
  return locale === "mm" ? "my" : "en";
}

export function localeCookieValue(locale: Locale, secure: boolean): string {
  const secureFlag = secure ? "; Secure" : "";
  return `${LOCALE_COOKIE}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax${secureFlag}`;
}

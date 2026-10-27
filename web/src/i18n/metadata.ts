import type { Metadata } from "next";
import { cookies } from "next/headers";

import { LOCALE_COOKIE, parseLocale, type Locale } from "@/i18n/locale";
import { translate } from "@/i18n/translate";

export async function getLocale(): Promise<Locale> {
  const jar = await cookies();
  return parseLocale(jar.get(LOCALE_COOKIE)?.value);
}

export async function pageMetadata(title: string): Promise<Metadata> {
  return { title: translate(await getLocale(), title) };
}

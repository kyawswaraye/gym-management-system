"use client";

import { createContext, useCallback, useContext, type ReactNode } from "react";

import type { Locale } from "@/i18n/locale";
import { translate, type Vars } from "@/i18n/translate";

const LocaleContext = createContext<Locale>("en");

export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: ReactNode;
}) {
  return (
    <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>
  );
}

export function useLocale(): Locale {
  return useContext(LocaleContext);
}

export function useTx(): (text: string, vars?: Vars) => string {
  const locale = useContext(LocaleContext);
  return useCallback(
    (text: string, vars?: Vars) => translate(locale, text, vars),
    [locale],
  );
}

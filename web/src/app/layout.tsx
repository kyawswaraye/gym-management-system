import type { Metadata } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";
import { cookies } from "next/headers";

import { LOCALE_COOKIE, htmlLang, parseLocale } from "@/i18n/locale";
import { translate } from "@/i18n/translate";
import { LocaleProvider } from "@/i18n/use-tx";

import "./globals.css";

const latin = localFont({
  src: "../fonts/noto-sans-latin.woff2",
  variable: "--font-latin",
  display: "swap",
  weight: "100 900",
  adjustFontFallback: false,
});

const myanmar = localFont({
  src: "../fonts/noto-sans-myanmar.woff2",
  variable: "--font-myanmar",
  display: "swap",
  weight: "100 900",
  adjustFontFallback: false,
});

export async function generateMetadata(): Promise<Metadata> {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return {
    title: {
      default: "GymPilot MM",
      template: "%s | GymPilot MM",
    },
    description: translate(locale, "Secure gym staff workspace"),
  };
}

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return (
    <html
      className={`${latin.variable} ${myanmar.variable}`}
      lang={htmlLang(locale)}
    >
      <body>
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}

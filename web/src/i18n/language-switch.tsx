"use client";

import { useRouter } from "next/navigation";

import { localeCookieValue, type Locale } from "@/i18n/locale";
import { useLocale, useTx } from "@/i18n/use-tx";

export function LanguageSwitch({
  className = "",
  variant = "bar",
}: {
  className?: string;
  variant?: "bar" | "inline";
}) {
  const locale = useLocale();
  const tx = useTx();
  const router = useRouter();
  const inline = variant === "inline";

  function choose(next: Locale) {
    if (next === locale) return;
    const secure = window.location.protocol === "https:";
    document.cookie = localeCookieValue(next, secure);
    router.refresh();
  }

  function optionClass(selected: boolean) {
    if (inline) {
      return `rounded-md px-2 py-1 leading-none transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff] ${
        selected
          ? "bg-[#0866ff] text-white"
          : "text-slate-500 hover:text-[#050505]"
      }`;
    }
    return `min-h-8 rounded-lg px-3 py-1.5 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff] ${
      selected
        ? "bg-white text-[#050505] shadow-sm"
        : "text-slate-500 hover:text-[#050505]"
    }`;
  }

  return (
    <div
      aria-label={tx("Language")}
      className={
        inline
          ? `inline-grid shrink-0 grid-cols-2 rounded-lg bg-white p-0.5 text-[11px] font-semibold ring-1 ring-[#e4e6eb] ${className}`
          : `grid grid-cols-2 gap-1 rounded-xl bg-[#e4e6eb] p-1 text-xs font-bold ${className}`
      }
      role="group"
    >
      <button
        aria-pressed={locale === "en"}
        className={optionClass(locale === "en")}
        onClick={() => choose("en")}
        type="button"
      >
        EN
      </button>
      <button
        aria-pressed={locale === "mm"}
        className={optionClass(locale === "mm")}
        onClick={() => choose("mm")}
        type="button"
      >
        MM
      </button>
    </div>
  );
}

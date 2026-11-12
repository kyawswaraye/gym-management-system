import type { ReactNode } from "react";

import { AuthLayoutLanguageSwitch } from "@/i18n/language-switch";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell relative flex min-h-dvh items-center justify-center overflow-hidden px-4 py-10">
      <div aria-hidden="true" className="auth-shell__base" />
      <div aria-hidden="true" className="auth-shell__emblem">
        <span className="auth-shell__emblem-ring" />
        <span className="auth-shell__emblem-arc" />
        <span className="auth-shell__emblem-arc auth-shell__emblem-arc--inner" />
      </div>
      <div aria-hidden="true" className="auth-shell__glow" />
      <div className="relative z-10 flex w-full min-w-0 max-w-md flex-col items-center gap-6">
        {children}
        <AuthLayoutLanguageSwitch />
      </div>
    </div>
  );
}

import type { ReactNode } from "react";

import { LanguageSwitch } from "@/i18n/language-switch";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh flex-col">
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {children}
      </div>
      <div className="mx-auto w-full max-w-xs shrink-0 px-6 pt-2 pb-6">
        <LanguageSwitch />
      </div>
    </div>
  );
}

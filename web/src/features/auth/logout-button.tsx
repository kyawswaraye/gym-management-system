"use client";
import { useTx } from "@/i18n/use-tx";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { getCsrfToken, mutationHeaders } from "@/features/auth/client";

export function LogoutButton({ className = "" }: { className?: string }) {
  const tx = useTx();
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    try {
      const csrf = await getCsrfToken();
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(csrf),
        body: "{}",
      });
    } finally {
      router.replace("/login");
      router.refresh();
      setPending(false);
    }
  }

  return (
    <button
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm font-medium leading-snug text-[#050505] transition-colors hover:bg-[#f2f2f2] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff] disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      type="button"
      onClick={logout}
      disabled={pending}
    >
      <svg
        aria-hidden="true"
        className="size-3.5 shrink-0"
        fill="none"
        viewBox="0 0 16 16"
      >
        <path
          d="M6.25 2.75H3.75a1 1 0 0 0-1 1v8.5a1 1 0 0 0 1 1h2.5"
          stroke="currentColor"
          strokeLinecap="round"
          strokeWidth="1.4"
        />
        <path
          d="M6.5 8h7M11 5.25 13.75 8 11 10.75"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.4"
        />
      </svg>
      {pending ? tx("Signing out…") : tx("Sign out")}
    </button>
  );
}

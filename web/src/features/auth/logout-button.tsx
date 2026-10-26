"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { getCsrfToken, mutationHeaders } from "@/features/auth/client";

export function LogoutButton({ className = "" }: { className?: string }) {
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
      className={`text-xs font-medium text-[#65676b] underline-offset-2 transition-colors hover:text-[#0866ff] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff] disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      type="button"
      onClick={logout}
      disabled={pending}
    >
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}

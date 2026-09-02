"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { getCsrfToken, mutationHeaders } from "@/features/auth/client";

export function LogoutButton() {
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
      className="rounded-md border border-slate-300 bg-white px-3 py-2 font-semibold"
      type="button"
      onClick={logout}
      disabled={pending}
    >
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}

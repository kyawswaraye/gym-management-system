"use client";

import { useEffect } from "react";

import { BrandLogo } from "@/components/brand-logo";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("A page failed to render.", error.digest);
  }, [error]);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card text-center">
        <BrandLogo />
        <h1 className="mt-6 text-2xl font-bold">Something went wrong</h1>
        <p className="muted mt-2">
          No sensitive details were displayed. Please try again.
        </p>
        <button className="button mt-5" type="button" onClick={reset}>
          Try again
        </button>
      </section>
    </main>
  );
}

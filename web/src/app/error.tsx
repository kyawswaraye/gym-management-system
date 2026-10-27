"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect } from "react";

import { BrandLogo } from "@/components/brand-logo";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const tx = useTx();
  useEffect(() => {
    console.error("A page failed to render.", error.digest);
  }, [error]);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card text-center">
        <BrandLogo />
        <h1 className="mt-6 text-2xl font-bold">
          {tx("Something went wrong")}
        </h1>
        <p className="muted mt-2">
          {" "}
          {tx("No sensitive details were displayed. Please try again.")}{" "}
        </p>
        <button className="button mt-5" type="button" onClick={reset}>
          {" "}
          {tx("Try again")}{" "}
        </button>
      </section>
    </main>
  );
}

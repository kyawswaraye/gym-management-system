import Link from "next/link";

import { BrandLogo } from "@/components/brand-logo";

export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card text-center">
        <BrandLogo />
        <h1 className="mt-6 text-2xl font-bold">Page not found</h1>
        <p className="muted mt-2">The requested page does not exist.</p>
        <Link className="mt-5 inline-block underline" href="/dashboard">
          Return to dashboard
        </Link>
      </section>
    </main>
  );
}

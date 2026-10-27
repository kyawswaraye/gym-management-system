import { Tx } from "@/i18n/tx";
import Link from "next/link";

import { BrandLogo } from "@/components/brand-logo";
import { LanguageSwitch } from "@/i18n/language-switch";

export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card text-center">
        <BrandLogo />
        <h1 className="mt-6 text-2xl font-bold">
          <Tx text="Page not found" />
        </h1>
        <p className="muted mt-2">
          <Tx text="The requested page does not exist." />
        </p>
        <Link className="mt-5 inline-block underline" href="/dashboard">
          {" "}
          <Tx text="Return to dashboard" />{" "}
        </Link>
        <LanguageSwitch className="mx-auto mt-6 max-w-xs" />
      </section>
    </main>
  );
}

import { Tx } from "@/i18n/tx";
import { redirect } from "next/navigation";

import { BrandLogo } from "@/components/brand-logo";
import { LanguageSwitch } from "@/i18n/language-switch";
import { MFASetup } from "@/features/auth/mfa-setup";
import { readCurrentUser } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function MFASetupPage() {
  const session = await readCurrentUser();
  if (!session) redirect("/login?reason=expired");

  return (
    <main className="mx-auto max-w-2xl p-6 md:p-10">
      <div className="flex items-start justify-between gap-4">
        <BrandLogo align="start" />
        <LanguageSwitch className="w-36 shrink-0" />
      </div>
      <h1 className="mt-6 text-3xl font-bold">
        <Tx text="Security" />
      </h1>
      <p className="muted mt-2">
        {" "}
        <Tx text="Protect sensitive Owner and platform actions." />{" "}
      </p>
      <div className="mt-6">
        <MFASetup />
      </div>
    </main>
  );
}

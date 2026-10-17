import { redirect } from "next/navigation";

import { MFASetup } from "@/features/auth/mfa-setup";
import { readCurrentUser } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function MFASetupPage() {
  const session = await readCurrentUser();
  if (!session) redirect("/login?reason=expired");

  return (
    <main className="mx-auto max-w-2xl p-6 md:p-10">
      <h1 className="text-3xl font-bold">Security</h1>
      <p className="muted mt-2">
        Protect sensitive Owner and platform actions.
      </p>
      <div className="mt-6">
        <MFASetup />
      </div>
    </main>
  );
}

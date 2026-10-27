import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";
import Link from "next/link";

import { BrandLogo } from "@/components/brand-logo";
import { ResetPasswordForm } from "@/features/auth/reset-password-form";

export async function generateMetadata() {
  return pageMetadata("Reset password");
}

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section className="card w-full max-w-md" aria-labelledby="reset-title">
        <BrandLogo />
        <h1 id="reset-title" className="mt-6 text-3xl font-bold">
          {" "}
          <Tx text="Reset password" />{" "}
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Choose a new password. You will need to sign in again afterward." />{" "}
        </p>
        {token ? (
          <ResetPasswordForm token={token} />
        ) : (
          <p className="error mt-5" role="alert">
            {" "}
            <Tx text="This reset link is incomplete." />{" "}
          </p>
        )}
        <p className="muted mt-6">
          <Link href="/login">
            <Tx text="Back to sign in" />
          </Link>
        </p>
      </section>
    </main>
  );
}

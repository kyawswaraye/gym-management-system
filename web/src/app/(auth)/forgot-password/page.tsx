import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";
import Link from "next/link";

import { BrandLogo } from "@/components/brand-logo";
import { ForgotPasswordForm } from "@/features/auth/forgot-password-form";

export async function generateMetadata() {
  return pageMetadata("Forgot password");
}

export default function ForgotPasswordPage() {
  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section className="card w-full max-w-md" aria-labelledby="forgot-title">
        <BrandLogo />
        <h1 id="forgot-title" className="mt-6 text-3xl font-bold">
          {" "}
          <Tx text="Forgot password" />{" "}
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Enter the email for your staff, owner, or linked member account. If an account exists, a reset link will be sent." />{" "}
        </p>
        <ForgotPasswordForm />
        <p className="muted mt-6">
          <Link href="/login">
            <Tx text="Back to sign in" />
          </Link>
        </p>
      </section>
    </main>
  );
}

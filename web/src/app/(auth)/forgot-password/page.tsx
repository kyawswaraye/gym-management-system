import type { Metadata } from "next";
import Link from "next/link";

import { BrandLogo } from "@/components/brand-logo";
import { ForgotPasswordForm } from "@/features/auth/forgot-password-form";

export const metadata: Metadata = { title: "Forgot password" };

export default function ForgotPasswordPage() {
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card w-full max-w-md" aria-labelledby="forgot-title">
        <BrandLogo />
        <h1 id="forgot-title" className="mt-6 text-3xl font-bold">
          Forgot password
        </h1>
        <p className="muted mt-2">
          Enter the email for your staff, owner, or linked member account. If an
          account exists, a reset link will be sent.
        </p>
        <ForgotPasswordForm />
        <p className="muted mt-6">
          <Link href="/login">Back to sign in</Link>
        </p>
      </section>
    </main>
  );
}

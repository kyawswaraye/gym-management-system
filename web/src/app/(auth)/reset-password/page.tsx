import type { Metadata } from "next";
import Link from "next/link";

import { ResetPasswordForm } from "@/features/auth/reset-password-form";

export const metadata: Metadata = { title: "Reset password" };

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card w-full max-w-md" aria-labelledby="reset-title">
        <p className="font-bold text-emerald-700">Gym Management</p>
        <h1 id="reset-title" className="mt-2 text-3xl font-bold">
          Reset password
        </h1>
        <p className="muted mt-2">
          Choose a new password. You will need to sign in again afterward.
        </p>
        {token ? (
          <ResetPasswordForm token={token} />
        ) : (
          <p className="error mt-5" role="alert">
            This reset link is incomplete.
          </p>
        )}
        <p className="muted mt-6">
          <Link href="/login">Back to sign in</Link>
        </p>
      </section>
    </main>
  );
}

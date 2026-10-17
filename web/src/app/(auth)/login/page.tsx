import type { Metadata } from "next";

import { LoginForm } from "@/features/auth/login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ reason?: string }>;
}) {
  const { reason } = await searchParams;
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="card" aria-labelledby="login-title">
        <p className="font-bold text-emerald-700">Gym Management</p>
        <h1 id="login-title" className="mt-2 text-3xl font-bold">
          Sign in
        </h1>
        <p className="muted mt-2">
          Use your authorized staff or platform administrator account.
        </p>
        <LoginForm sessionExpired={reason === "expired"} />
      </section>
    </main>
  );
}

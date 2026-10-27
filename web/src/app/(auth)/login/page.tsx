import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";

import { BrandLogo } from "@/components/brand-logo";
import { LoginForm } from "@/features/auth/login-form";

export async function generateMetadata() {
  return pageMetadata("Sign in");
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ reason?: string }>;
}) {
  const { reason } = await searchParams;
  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section className="card" aria-labelledby="login-title">
        <BrandLogo />
        <h1 id="login-title" className="mt-6 text-3xl font-bold">
          {" "}
          <Tx text="Sign in" />{" "}
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Use your authorized staff or platform administrator account." />{" "}
        </p>
        <LoginForm sessionExpired={reason === "expired"} />
      </section>
    </main>
  );
}

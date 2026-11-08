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
    <main aria-labelledby="login-title" className="login-panel w-full bg-surface text-center">
      <div className="login-panel__brand px-8 pt-10 pb-6 sm:px-10">
        <BrandLogo />
        <div
          aria-hidden="true"
          className="mx-auto mt-5 h-px w-12 bg-[rgb(30_75_148/35%)]"
        />
        <h1
          className="mt-4 text-[1.7rem] leading-tight font-semibold text-[#1e4b94]"
          id="login-title"
        >
          <Tx text="Sign in" />
        </h1>
        <p className="muted mt-2">
          <Tx text="Use your authorized staff or platform administrator account." />
        </p>
      </div>
      <div aria-hidden="true" className="login-wave">
        <svg className="h-10 w-full" preserveAspectRatio="none" viewBox="0 0 480 48">
          <path
            d="M0 24 C80 8 140 40 240 24 C340 8 400 40 480 24 L480 48 L0 48 Z"
            fill="#f4f7fb"
          />
        </svg>
      </div>
      <div className="rounded-b-[3rem] bg-[#f4f7fb] px-8 pt-2 pb-9 text-left sm:px-10 sm:pb-10">
        <LoginForm sessionExpired={reason === "expired"} />
      </div>
    </main>
  );
}
